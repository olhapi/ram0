// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import type { ImportJournal, ImportPlan, ImportRecord, ImportSummary, MemoryImportBackend } from "./model"

export class SupermemoryImportBackend implements MemoryImportBackend {
	private readonly baseUrl: string

	constructor(baseUrl: string, private readonly apiKey: string) {
		this.baseUrl = baseUrl.replace(/\/$/, "")
	}

	async createMemories(containerTag: string, records: ImportRecord[]): Promise<Array<{ key: string; id: string }>> {
		const response = await fetch(`${this.baseUrl}/v4/memories`, {
			method: "POST",
			headers: { Authorization: `Bearer ${this.apiKey}`, "content-type": "application/json" },
			body: JSON.stringify({
				containerTag,
				memories: records.map((record) => ({
					content: record.content,
					metadata: record.metadata,
					...(record.isStatic === undefined ? {} : { isStatic: record.isStatic }),
					...(record.forgetAfter === undefined ? {} : { forgetAfter: record.forgetAfter }),
				})),
			}),
			signal: AbortSignal.timeout(60_000),
		})
		if (!response.ok) throw new Error(`Supermemory import failed with HTTP ${response.status}`)
		const value = (await response.json()) as { memories?: unknown[] }
		if (!Array.isArray(value.memories)) throw new Error("Supermemory import returned an invalid response")
		return value.memories.map((item, index) => {
			const id = item && typeof item === "object" ? Reflect.get(item, "id") : undefined
			if (typeof id !== "string" || !records[index]) throw new Error("Supermemory import returned an invalid memory acknowledgement")
			return { key: records[index].key, id }
		})
	}
}

export async function importPlan(
	plan: ImportPlan,
	journal: ImportJournal,
	backend: MemoryImportBackend,
): Promise<ImportSummary> {
	const pending = plan.records.filter((record) => !journal.completedKeys.has(record.key))
	const skipped = plan.records.length - pending.length
	const byContainer = new Map<string, typeof pending>()
	for (const record of pending) {
		const current = byContainer.get(record.containerTag) ?? []
		current.push(record)
		byContainer.set(record.containerTag, current)
	}

	let imported = 0
	const failed: ImportSummary["failed"] = []
	for (const [containerTag, records] of byContainer) {
		for (let offset = 0; offset < records.length; offset += 100) {
			const batch = records.slice(offset, offset + 100)
			try {
				const acknowledged = await backend.createMemories(containerTag, batch)
				const expectedKeys = batch.map((record) => record.key)
				if (acknowledged.length !== batch.length || acknowledged.some((item, index) => item.key !== expectedKeys[index] || !item.id)) {
					throw new Error("Supermemory returned an incomplete batch acknowledgement")
				}
				await journal.append({
					containerTag,
					keys: expectedKeys,
					targetIds: acknowledged.map((item) => item.id),
					completedAt: new Date().toISOString(),
				})
				imported += batch.length
			} catch (error) {
				failed.push({ keys: batch.map((record) => record.key), reason: error instanceof Error ? error.message : "unknown import error" })
			}
		}
	}

	return { imported, skipped, failed }
}
