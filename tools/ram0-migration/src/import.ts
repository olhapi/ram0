// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import type {
	ImportJournal,
	ImportPlan,
	ImportRecord,
	ImportSummary,
	MemoryImportBackend,
} from "./model"
import { matchingDestination, SupermemoryCoverageBackend } from "./verify"

export class SupermemoryImportBackend
	extends SupermemoryCoverageBackend
	implements MemoryImportBackend
{
	async createMemories(
		containerTag: string,
		records: ImportRecord[],
	): Promise<Array<{ key: string; id: string }>> {
		const response = await fetch(`${this.baseUrl}/v4/memories`, {
			method: "POST",
			headers: {
				Authorization: `Bearer ${this.apiKey}`,
				"content-type": "application/json",
			},
			body: JSON.stringify({
				containerTag,
				memories: records.map((record) => ({
					content: record.content,
					metadata: { ...record.metadata, ram0_migration_key: record.key },
					...(record.isStatic === undefined
						? {}
						: { isStatic: record.isStatic }),
					...(record.forgetAfter === undefined
						? {}
						: { forgetAfter: record.forgetAfter }),
				})),
			}),
			signal: AbortSignal.timeout(60_000),
		})
		if (!response.ok)
			throw new Error(`Supermemory import failed with HTTP ${response.status}`)
		const value = (await response.json()) as { memories?: unknown[] }
		if (!Array.isArray(value.memories))
			throw new Error("Supermemory import returned an invalid response")
		return value.memories.map((item, index) => {
			const id =
				item && typeof item === "object" ? Reflect.get(item, "id") : undefined
			if (typeof id !== "string" || !records[index])
				throw new Error(
					"Supermemory import returned an invalid memory acknowledgement",
				)
			return { key: records[index].key, id }
		})
	}
}

export async function importPlan(
	plan: ImportPlan,
	journal: ImportJournal,
	backend: MemoryImportBackend,
): Promise<ImportSummary> {
	const pending = plan.records
	let skipped = 0
	const byContainer = new Map<string, typeof pending>()
	for (const record of pending) {
		const current = byContainer.get(record.containerTag) ?? []
		current.push(record)
		byContainer.set(record.containerTag, current)
	}

	let imported = 0
	const failed: ImportSummary["failed"] = []
	for (const [containerTag, records] of byContainer) {
		let destination: Awaited<ReturnType<MemoryImportBackend["listMemories"]>>
		try {
			destination = await backend.listMemories(containerTag)
		} catch {
			failed.push({
				keys: records.map((record) => record.key),
				reason: "Destination reconciliation failed; no writes attempted",
			})
			continue
		}
		for (let offset = 0; offset < records.length; offset += 100) {
			const batch = records.slice(offset, offset + 100)
			try {
				const existing = batch.map((record) =>
					matchingDestination(record, destination),
				)
				if (
					batch.some(
						(record, index) =>
							!existing[index] &&
							(journal.completedKeys.has(record.key) ||
								destination.some(
									(row) => row.metadata?.ram0_migration_key === record.key,
								)),
					)
				) {
					throw new Error(
						"Destination conflicts with migration identity or journal; manual reconciliation required",
					)
				}
				const missing = batch.filter((_record, index) => !existing[index])
				const created = missing.length
					? await backend.createMemories(containerTag, missing)
					: []
				const acknowledged = batch.map((record, index) => {
					const row = existing[index]
					return row
						? { key: record.key, id: row.id }
						: created.find((item) => item.key === record.key)
				})
				const expectedKeys = batch.map((record) => record.key)
				if (
					created.length !== missing.length ||
					acknowledged.some(
						(item, index) =>
							!item || item.key !== expectedKeys[index] || !item.id,
					)
				) {
					throw new Error(
						"Supermemory returned an incomplete batch acknowledgement",
					)
				}
				await journal.append({
					containerTag,
					keys: expectedKeys,
					targetIds: acknowledged.map((item) => item?.id ?? ""),
					completedAt: new Date().toISOString(),
				})
				imported += missing.length
				skipped += batch.length - missing.length
			} catch (error) {
				failed.push({
					keys: batch.map((record) => record.key),
					reason:
						error instanceof Error ? error.message : "unknown import error",
				})
			}
		}
	}

	return { imported, skipped, failed }
}
