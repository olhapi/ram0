// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { createServer } from "node:http"
import type { AddressInfo } from "node:net"
import { describe, expect, test } from "vitest"

import type { ImportPlan } from "./model"
import {
	SupermemoryCoverageBackend,
	verifyImportedPlan,
	type DestinationMemory,
} from "./verify"

const plan: ImportPlan = {
	version: 1,
	sourceCount: 3,
	exactDuplicates: 0,
	invalid: [],
	records: [
		{ key: "1", content: "one", containerTag: "personal", metadata: {} },
		{ key: "2", content: "two", containerTag: "personal", metadata: {} },
		{
			key: "3",
			content: "three",
			containerTag: "repo_demo__1234",
			metadata: {},
		},
	],
}

describe("verifyImportedPlan", () => {
	for (const [label, rows] of [
		["unrelated rows", [{ id: "a", memory: "one", metadata: {} }]],
		[
			"duplicate identities",
			[
				{
					id: "a",
					memory: "one",
					metadata: { ram0_migration_key: "1", ram0_source_ids: ["source-1"] },
				},
				{
					id: "b",
					memory: "one",
					metadata: { ram0_migration_key: "1", ram0_source_ids: ["source-1"] },
				},
			],
		],
		[
			"missing provenance",
			[{ id: "a", memory: "one", metadata: { ram0_migration_key: "1" } }],
		],
		[
			"changed content",
			[
				{
					id: "a",
					memory: "other",
					metadata: { ram0_migration_key: "1", ram0_source_ids: ["source-1"] },
				},
			],
		],
	] as Array<[string, DestinationMemory[]]>) {
		test(`does not count ${label} as source coverage`, async () => {
			const single: ImportPlan = {
				...plan,
				records: [
					{
						key: "1",
						content: "one",
						containerTag: "personal",
						metadata: { ram0_source_ids: ["source-1"] },
					},
				],
			}
			expect(
				(await verifyImportedPlan(single, { listMemories: async () => rows }))
					.containers,
			).toEqual([{ containerTag: "personal", expected: 1, actual: 0 }])
		})
	}

	test("reads every direct-memory page before accepting destination coverage", async () => {
		const pages: number[] = []
		const server = createServer(async (request, response) => {
			const chunks: Buffer[] = []
			for await (const chunk of request) chunks.push(Buffer.from(chunk))
			const body = JSON.parse(Buffer.concat(chunks).toString())
			pages.push(body.page)
			const record = plan.records[body.page - 1]
			response.end(
				JSON.stringify({
					memoryEntries: [
						{
							id: `target-${record?.key}`,
							memory: record?.content,
							metadata: { ram0_migration_key: record?.key },
						},
					],
					pagination: { currentPage: body.page, totalPages: 2, totalItems: 2 },
				}),
			)
		})
		await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
		try {
			const result = await verifyImportedPlan(
				{ ...plan, records: plan.records.slice(0, 2) },
				new SupermemoryCoverageBackend(
					`http://127.0.0.1:${(server.address() as AddressInfo).port}`,
					"synthetic-test-key",
				),
			)
			expect(result.complete).toBe(true)
			expect(pages).toEqual([1, 2])
		} finally {
			await new Promise<void>((resolve) => server.close(() => resolve()))
		}
	})
	test("unrelated rows and duplicate identities cannot replace source coverage", async () => {
		const result = await verifyImportedPlan(plan, {
			listMemories: async () => [
				{ id: "a", memory: "one", metadata: { ram0_migration_key: "1" } },
				{ id: "b", memory: "one", metadata: { ram0_migration_key: "1" } },
				{ id: "unrelated", memory: "elsewhere", metadata: {} },
			],
		})
		expect(result.complete).toBe(false)
	})
	test("checks each target container through authenticated v4 source coverage", async () => {
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
					memoryEntries: plan.records
						.filter((record) => record.containerTag === container)
						.map((record) => ({
							id: `target-${record.key}`,
							memory: record.content,
							metadata: { ram0_migration_key: record.key },
						})),
					pagination: {
						currentPage: 1,
						limit: 100,
						totalItems: container === "personal" ? 2 : 1,
						totalPages: 1,
					},
				}),
			)
		})
		await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
		const { port } = server.address() as AddressInfo
		try {
			const result = await verifyImportedPlan(
				plan,
				new SupermemoryCoverageBackend(`http://127.0.0.1:${port}`, "secret"),
			)
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
					body: {
						containerTags: ["personal"],
						page: 1,
						limit: 100,
						sort: "createdAt",
						order: "asc",
					},
				},
				{
					authorization: "Bearer secret",
					body: {
						containerTags: ["repo_demo__1234"],
						page: 1,
						limit: 100,
						sort: "createdAt",
						order: "asc",
					},
				},
			])
		} finally {
			await new Promise<void>((resolve, reject) =>
				server.close((error) => (error ? reject(error) : resolve())),
			)
		}
	})

	test("reports an incomplete container without exposing memory content", async () => {
		const result = await verifyImportedPlan(plan, {
			listMemories: async () => [],
		})
		expect(result.complete).toBe(false)
		expect(result.containers.every((item) => item.actual === 0)).toBe(true)
	})
})
