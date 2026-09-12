// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { isDeepStrictEqual } from "node:util"
import type { ImportPlan, ImportRecord } from "./model"

export interface DestinationMemory {
	id: string
	memory: string
	metadata?: Record<string, unknown> | null
	isForgotten?: boolean
	isLatest?: boolean
}

export interface MemoryCoverageBackend {
	listMemories(containerTag: string): Promise<DestinationMemory[]>
}

export interface ImportVerification {
	complete: boolean
	containers: Array<{ containerTag: string; expected: number; actual: number }>
}

// A matching key alone is insufficient: verify the source text and provenance too.
export function matchingDestination(
	record: ImportRecord,
	rows: DestinationMemory[],
): DestinationMemory | undefined {
	const matches = rows.filter(
		(row) => row.metadata?.ram0_migration_key === record.key,
	)
	if (matches.length !== 1) return undefined
	const row = matches[0]
	if (
		!row ||
		row.memory !== record.content ||
		row.isForgotten ||
		row.isLatest === false
	)
		return undefined
	if (
		!Object.entries(record.metadata).every(([key, value]) =>
			isDeepStrictEqual(row.metadata?.[key], value),
		)
	)
		return undefined
	return row
}

export class SupermemoryCoverageBackend implements MemoryCoverageBackend {
	protected readonly baseUrl: string
	constructor(
		baseUrl: string,
		protected readonly apiKey: string,
	) {
		this.baseUrl = baseUrl.replace(/\/$/, "")
	}

	async listMemories(containerTag: string): Promise<DestinationMemory[]> {
		const rows: DestinationMemory[] = []
		let expectedTotal: number | undefined
		for (let page = 1; ; page++) {
			const response = await fetch(`${this.baseUrl}/v4/memories/list`, {
				method: "POST",
				headers: {
					Authorization: `Bearer ${this.apiKey}`,
					"content-type": "application/json",
				},
				body: JSON.stringify({
					containerTags: [containerTag],
					page,
					limit: 100,
					sort: "createdAt",
					order: "asc",
				}),
				signal: AbortSignal.timeout(30_000),
			})
			if (!response.ok)
				throw new Error(
					`Supermemory listing failed with HTTP ${response.status}`,
				)
			const value = (await response.json()) as {
				memoryEntries?: DestinationMemory[]
				pagination?: {
					totalItems?: number
					totalPages?: number
					currentPage?: number
				}
			}
			const total = value.pagination?.totalItems
			const pages = value.pagination?.totalPages
			if (
				!Array.isArray(value.memoryEntries) ||
				typeof total !== "number" ||
				!Number.isInteger(total) ||
				total < 0 ||
				typeof pages !== "number" ||
				!Number.isInteger(pages) ||
				pages < 0 ||
				value.pagination?.currentPage !== page
			)
				throw new Error("Invalid destination listing")
			if (expectedTotal !== undefined && expectedTotal !== total)
				throw new Error(
					"Destination changed during reconciliation; keep all other writers stopped",
				)
			expectedTotal = total
			for (const row of value.memoryEntries) {
				if (
					!row ||
					typeof row.id !== "string" ||
					typeof row.memory !== "string"
				)
					throw new Error("Invalid destination memory")
				rows.push(row)
			}
			if (page >= pages) break
			if (value.memoryEntries.length === 0)
				throw new Error("Incomplete destination listing")
		}
		if (
			rows.length !== expectedTotal ||
			new Set(rows.map((row) => row.id)).size !== rows.length
		)
			throw new Error("Incomplete or duplicate destination listing")
		return rows
	}
}

export async function verifyImportedPlan(
	plan: ImportPlan,
	backend: MemoryCoverageBackend,
): Promise<ImportVerification> {
	const containers: ImportVerification["containers"] = []
	for (const containerTag of new Set(
		plan.records.map((record) => record.containerTag),
	)) {
		const records = plan.records.filter(
			(record) => record.containerTag === containerTag,
		)
		const rows = await backend.listMemories(containerTag)
		containers.push({
			containerTag,
			expected: records.length,
			actual: records.filter((record) => matchingDestination(record, rows))
				.length,
		})
	}
	return {
		complete:
			plan.invalid.length === 0 &&
			containers.every(({ actual, expected }) => actual === expected),
		containers,
	}
}
