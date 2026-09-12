// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { createServer } from "node:http"
import type { AddressInfo } from "node:net"
import { afterEach, describe, expect, test } from "vitest"

import { SupermemoryBackend } from "./backend"

interface CapturedRequest {
	method: string
	path: string
	authorization?: string
	body?: unknown
}

const cleanup: Array<() => Promise<void>> = []

afterEach(async () => {
	await Promise.all(cleanup.splice(0).map((close) => close()))
})

async function createApi(): Promise<{ baseUrl: string; requests: CapturedRequest[] }> {
	const requests: CapturedRequest[] = []
	const server = createServer(async (request, response) => {
		const chunks: Buffer[] = []
		for await (const chunk of request) chunks.push(Buffer.from(chunk))
		const rawBody = Buffer.concat(chunks).toString("utf8")
		requests.push({
			method: request.method ?? "",
			path: request.url ?? "",
			authorization: request.headers.authorization,
			...(rawBody ? { body: JSON.parse(rawBody) } : {}),
		})

		response.setHeader("content-type", "application/json")
		switch (`${request.method} ${request.url}`) {
			case "POST /v4/search":
				response.end(
					JSON.stringify({
						results: [
							{ id: "memory-1", memory: "Remember me", similarity: 0.81 },
							{ id: "chunk-1", chunk: "Document excerpt", similarity: 0.72 },
						],
						total: 2,
						timing: 4,
					}),
				)
				break
			case "POST /v4/profile":
				response.end(JSON.stringify({ profile: { static: ["Stable"], dynamic: ["Recent"] } }))
				break
			case "POST /v3/documents":
				response.end(JSON.stringify({ id: "document-1", status: "queued" }))
				break
			case "DELETE /v4/memories":
				response.end(JSON.stringify({ id: "memory-1", forgotten: true }))
				break
			case "POST /v4/memories/list":
				response.end(JSON.stringify({ memoryEntries: [], pagination: { currentPage: 1, totalItems: 0, totalPages: 0 } }))
				break
			case "POST /v3/documents/list":
				response.end(JSON.stringify({ memories: [], pagination: { currentPage: 1, totalItems: 0, totalPages: 0 } }))
				break
			case "GET /v3/documents/document-1":
				response.end(JSON.stringify({ id: "document-1", content: "Source" }))
				break
			case "GET /v3/container-tags/list":
				response.end(JSON.stringify([{ containerTag: "personal", memoryCount: 1 }]))
				break
			default:
				response.statusCode = 404
				response.end(JSON.stringify({ error: "not found" }))
		}
	})
	await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
	cleanup.push(() => new Promise<void>((resolve, reject) => server.close((error) => (error ? reject(error) : resolve()))))
	const { port } = server.address() as AddressInfo
	return { baseUrl: `http://127.0.0.1:${port}`, requests }
}

describe("SupermemoryBackend", () => {
	test("maps the local REST API into the gateway contract", async () => {
		const api = await createApi()
		const backend = new SupermemoryBackend(api.baseUrl, "engine-secret")

		expect(await backend.search("deploy", "personal", 7)).toEqual([
			{ id: "memory-1", text: "Remember me", score: 0.81, containerTag: "personal", kind: "memory" },
			{ id: "chunk-1", text: "Document excerpt", score: 0.72, containerTag: "personal", kind: "chunk" },
		])
		expect(await backend.profile("deploy", "personal")).toEqual({ static: ["Stable"], dynamic: ["Recent"] })
		expect(await backend.add("New fact", "personal", { source: "migration" })).toEqual({ id: "document-1" })
		expect(await backend.forget("Old fact", "personal")).toEqual({ id: "memory-1", success: true })
		expect(await backend.listMemories("personal", 20)).toMatchObject({ memoryEntries: [] })
		expect(await backend.listDocuments("personal", 20)).toMatchObject({ memories: [] })
		expect(await backend.getDocument("document-1")).toMatchObject({ content: "Source" })
		expect(await backend.listSpaces()).toEqual([{ containerTag: "personal", memoryCount: 1 }])

		expect(api.requests).toEqual([
			{ method: "POST", path: "/v4/search", authorization: "Bearer engine-secret", body: { q: "deploy", containerTag: "personal", limit: 7, searchMode: "hybrid" } },
			{ method: "POST", path: "/v4/profile", authorization: "Bearer engine-secret", body: { q: "deploy", containerTag: "personal" } },
			{ method: "POST", path: "/v3/documents", authorization: "Bearer engine-secret", body: { content: "New fact", containerTag: "personal", metadata: { source: "migration" } } },
			{ method: "DELETE", path: "/v4/memories", authorization: "Bearer engine-secret", body: { content: "Old fact", containerTag: "personal" } },
			{ method: "POST", path: "/v4/memories/list", authorization: "Bearer engine-secret", body: { containerTags: ["personal"], page: 1, limit: 20, sort: "createdAt", order: "desc" } },
			{ method: "POST", path: "/v3/documents/list", authorization: "Bearer engine-secret", body: { containerTags: ["personal"], page: 1, limit: 20, sort: "createdAt", order: "desc", includeContent: false } },
			{ method: "GET", path: "/v3/documents/document-1", authorization: "Bearer engine-secret" },
			{ method: "GET", path: "/v3/container-tags/list", authorization: "Bearer engine-secret" },
		])
	})
})
