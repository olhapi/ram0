// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { createServer } from "node:http"
import { afterEach, describe, expect, test } from "vitest"

import type { MemoryBackend } from "./backend"
import { createGatewayServer } from "./server"
import { closeServer, listen } from "./test-utils"

const cleanup: Array<() => Promise<void>> = []

afterEach(async () => {
	await Promise.all(cleanup.splice(0).map((close) => close()))
})

const unusedBackend: MemoryBackend = {
	search: async () => [],
	profile: async () => ({ static: [], dynamic: [] }),
	add: async () => ({ id: "unused" }),
	forget: async () => ({ success: false }),
	listMemories: async () => ({}),
	listDocuments: async () => ({}),
	getDocument: async () => ({}),
	listSpaces: async () => [],
}

describe("gateway HTTP routing", () => {
	test("reports ready when the engine root is reachable", async () => {
		let probedPath = ""
		const engine = createServer((request, response) => {
			probedPath = request.url ?? ""
			response.statusCode = request.url === "/" ? 200 : 404
			response.end()
		})
		const engineUrl = await listen(engine)
		cleanup.push(() => closeServer(engine))
		const gateway = createGatewayServer(
			{ host: "127.0.0.1", port: 0, engineUrl, apiKey: "gateway-secret", personalContainer: "personal" },
			unusedBackend,
		)
		const baseUrl = await listen(gateway)
		cleanup.push(() => closeServer(gateway))

		const response = await fetch(`${baseUrl}/health`)

		expect(response.status).toBe(200)
		expect(await response.json()).toEqual({ status: "ok", engine: "ok" })
		expect(probedPath).toBe("/")
	})

	test("rejects MCP requests without the configured bearer token", async () => {
		const gateway = createGatewayServer(
			{ host: "127.0.0.1", port: 0, engineUrl: "http://127.0.0.1:1", apiKey: "gateway-secret", personalContainer: "personal" },
			unusedBackend,
		)
		const baseUrl = await listen(gateway)
		cleanup.push(() => closeServer(gateway))

		const response = await fetch(`${baseUrl}/mcp`, {
			method: "POST",
			headers: { "content-type": "application/json" },
			body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }),
		})

		expect(response.status).toBe(401)
		expect(response.headers.get("www-authenticate")).toBe("Bearer")
	})

	test("streams REST requests and responses to the local engine", async () => {
		let captured = ""
		const engine = createServer(async (request, response) => {
			for await (const chunk of request) captured += chunk.toString()
			response.statusCode = 202
			response.setHeader("content-type", "application/json")
			response.end(JSON.stringify({ path: request.url, authorization: request.headers.authorization }))
		})
		const engineUrl = await listen(engine)
		cleanup.push(() => closeServer(engine))
		const gateway = createGatewayServer(
			{ host: "127.0.0.1", port: 0, engineUrl, apiKey: "gateway-secret", personalContainer: "personal" },
			unusedBackend,
		)
		const baseUrl = await listen(gateway)
		cleanup.push(() => closeServer(gateway))

		const response = await fetch(`${baseUrl}/v4/profile?source=test`, {
			method: "POST",
			headers: { Authorization: "Bearer gateway-secret", "content-type": "application/json" },
			body: JSON.stringify({ containerTag: "personal" }),
		})

		expect(response.status).toBe(202)
		expect(await response.json()).toEqual({ path: "/v4/profile?source=test", authorization: "Bearer gateway-secret" })
		expect(captured).toBe('{"containerTag":"personal"}')
	})
})
