// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { createServer } from "node:http"
import type { AddressInfo } from "node:net"
import { describe, expect, test } from "vitest"

import type { ImportJournal, ImportPlan, ImportRecord, MemoryImportBackend } from "./model"
import { importPlan, SupermemoryImportBackend } from "./import"

function record(index: number, containerTag = "personal"): ImportRecord {
	return {
		key: `key-${String(index).padStart(3, "0")}`,
		content: `Memory ${index}`,
		containerTag,
		metadata: { ram0_source_ids: [`source-${index}`] },
	}
}

function plan(records: ImportRecord[]): ImportPlan {
	return { version: 1, sourceCount: records.length, exactDuplicates: 0, invalid: [], records }
}

function journal(): ImportJournal {
	const completedKeys = new Set<string>()
	return {
		completedKeys,
		append: async (entry) => {
			for (const key of entry.keys) completedKeys.add(key)
		},
	}
}

describe("importPlan", () => {
	test("uses the authenticated v4 direct-memory batch contract", async () => {
		let captured: { authorization?: string; body?: unknown } = {}
		const server = createServer(async (request, response) => {
			const chunks: Buffer[] = []
			for await (const chunk of request) chunks.push(Buffer.from(chunk))
			captured = {
				authorization: request.headers.authorization,
				body: JSON.parse(Buffer.concat(chunks).toString("utf8")),
			}
			response.setHeader("content-type", "application/json")
			response.statusCode = 201
			response.end(JSON.stringify({ documentId: null, memories: [{ id: "target-1" }, { id: "target-2" }] }))
		})
		await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
		const { port } = server.address() as AddressInfo
		try {
			const records = [record(1), { ...record(2), isStatic: true, forgetAfter: "2027-01-01T00:00:00Z" }]
			const backend = new SupermemoryImportBackend(`http://127.0.0.1:${port}`, "engine-secret")

			expect(await backend.createMemories("personal", records)).toEqual([
				{ key: "key-001", id: "target-1" },
				{ key: "key-002", id: "target-2" },
			])
			expect(captured).toEqual({
				authorization: "Bearer engine-secret",
				body: {
					containerTag: "personal",
					memories: [
						{ content: "Memory 1", metadata: { ram0_source_ids: ["source-1"] } },
						{
							content: "Memory 2",
							metadata: { ram0_source_ids: ["source-2"] },
							isStatic: true,
							forgetAfter: "2027-01-01T00:00:00Z",
						},
					],
				},
			})
		} finally {
			await new Promise<void>((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())))
		}
	})

	test("journals acknowledged batches and skips them on resume", async () => {
		const currentJournal = journal()
		const calls: ImportRecord[][] = []
		const backend: MemoryImportBackend = {
			createMemories: async (_container, records) => {
				calls.push(records)
				return records.map((item) => ({ key: item.key, id: `imported-${item.key}` }))
			},
		}
		const currentPlan = plan([record(1), record(2), record(3, "repo_atlas__1234abcd")])

		expect(await importPlan(currentPlan, currentJournal, backend)).toEqual({ imported: 3, skipped: 0, failed: [] })
		expect(await importPlan(currentPlan, currentJournal, backend)).toEqual({ imported: 0, skipped: 3, failed: [] })
		expect(calls).toHaveLength(2)
	})

	test("never sends more than 100 memories in one v4 request", async () => {
		const batchSizes: number[] = []
		const backend: MemoryImportBackend = {
			createMemories: async (_container, records) => {
				batchSizes.push(records.length)
				return records.map((item) => ({ key: item.key, id: `imported-${item.key}` }))
			},
		}

		const result = await importPlan(plan(Array.from({ length: 205 }, (_, index) => record(index))), journal(), backend)

		expect(result.imported).toBe(205)
		expect(batchSizes).toEqual([100, 100, 5])
	})
})
