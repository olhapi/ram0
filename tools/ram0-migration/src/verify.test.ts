// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { createServer } from "node:http"
import type { AddressInfo } from "node:net"
import { describe, expect, test } from "vitest"

import type { ImportPlan } from "./model"
import { SupermemoryCountBackend, verifyImportedPlan } from "./verify"

const plan: ImportPlan = {
	version: 1,
	sourceCount: 3,
	exactDuplicates: 0,
	invalid: [],
	records: [
		{ key: "1", content: "one", containerTag: "personal", metadata: {} },
		{ key: "2", content: "two", containerTag: "personal", metadata: {} },
		{ key: "3", content: "three", containerTag: "repo_demo__1234", metadata: {} },
	],
}

describe("verifyImportedPlan", () => {
	test("checks each target container through the authenticated v4 count", async () => {
		const requests: Array<{ authorization?: string; body: unknown }> = []
		const server = createServer(async (request, response) => {
			const chunks: Buffer[] = []
			for await (const chunk of request) chunks.push(Buffer.from(chunk))
			const body = JSON.parse(Buffer.concat(chunks).toString("utf8"))
			requests.push({ authorization: request.headers.authorization, body })
			const container = body.containerTags[0]
			response.setHeader("content-type", "application/json")
			response.end(
				JSON.stringify({
					memoryEntries: [],
					pagination: { currentPage: 1, limit: 1, totalItems: container === "personal" ? 2 : 1, totalPages: 1 },
				}),
			)
		})
		await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
		const { port } = server.address() as AddressInfo
		try {
			const result = await verifyImportedPlan(plan, new SupermemoryCountBackend(`http://127.0.0.1:${port}`, "secret"))
			expect(result).toEqual({
				complete: true,
				containers: [
					{ containerTag: "personal", expected: 2, actual: 2 },
					{ containerTag: "repo_demo__1234", expected: 1, actual: 1 },
				],
			})
			expect(requests).toEqual([
				{
					authorization: "Bearer secret",
					body: { containerTags: ["personal"], page: 1, limit: 1, sort: "createdAt", order: "desc" },
				},
				{
					authorization: "Bearer secret",
					body: { containerTags: ["repo_demo__1234"], page: 1, limit: 1, sort: "createdAt", order: "desc" },
				},
			])
		} finally {
			await new Promise<void>((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())))
		}
	})

	test("reports an incomplete container without exposing memory content", async () => {
		const result = await verifyImportedPlan(plan, { countMemories: async () => 0 })
		expect(result.complete).toBe(false)
		expect(result.containers.every((item) => item.actual === 0)).toBe(true)
	})
})
