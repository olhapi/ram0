// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import type { ImportPlan } from "./model"

export interface MemoryCountBackend {
	countMemories(containerTag: string): Promise<number>
}

export interface ImportVerification {
	complete: boolean
	containers: Array<{ containerTag: string; expected: number; actual: number }>
}

export class SupermemoryCountBackend implements MemoryCountBackend {
	private readonly baseUrl: string

	constructor(baseUrl: string, private readonly apiKey: string) {
		this.baseUrl = baseUrl.replace(/\/$/, "")
	}

	async countMemories(containerTag: string): Promise<number> {
		const response = await fetch(`${this.baseUrl}/v4/memories/list`, {
			method: "POST",
			headers: { Authorization: `Bearer ${this.apiKey}`, "content-type": "application/json" },
			body: JSON.stringify({ containerTags: [containerTag], page: 1, limit: 1, sort: "createdAt", order: "desc" }),
			signal: AbortSignal.timeout(30_000),
		})
		if (!response.ok) throw new Error(`Supermemory verification failed with HTTP ${response.status}`)
		const value = (await response.json()) as { pagination?: { totalItems?: unknown } }
		const total = value.pagination?.totalItems
		if (typeof total !== "number" || !Number.isInteger(total) || total < 0) {
			throw new Error("Supermemory verification returned an invalid count")
		}
		return total
	}
}

export async function verifyImportedPlan(plan: ImportPlan, backend: MemoryCountBackend): Promise<ImportVerification> {
	const expectedByContainer = new Map<string, number>()
	for (const record of plan.records) {
		expectedByContainer.set(record.containerTag, (expectedByContainer.get(record.containerTag) ?? 0) + 1)
	}
	const containers: ImportVerification["containers"] = []
	for (const [containerTag, expected] of expectedByContainer) {
		containers.push({ containerTag, expected, actual: await backend.countMemories(containerTag) })
	}
	return { complete: containers.every(({ actual, expected }) => actual >= expected), containers }
}
