// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { Client } from "@modelcontextprotocol/sdk/client/index.js"
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js"
import { afterEach, describe, expect, test } from "vitest"

import type { MemoryBackend } from "./backend"
import { createGatewayServer } from "./server"
import { closeServer, listen } from "./test-utils"

const cleanup: Array<() => Promise<void>> = []

afterEach(async () => {
	await Promise.all(cleanup.splice(0).map((close) => close()))
})

function backend(): MemoryBackend {
	return {
		search: async (_query, container) => [
			{
				id: `${container}-result`,
				text: `${container} deployment fact`,
				score: container === "personal" ? 0.7 : 0.9,
				containerTag: container,
				kind: "memory",
			},
		],
		profile: async (_query, container) => ({ static: [`${container} profile`], dynamic: [] }),
		add: async () => ({ id: "created-document" }),
		forget: async () => ({ id: "forgotten-memory", success: true }),
		listMemories: async () => ({ memoryEntries: [] }),
		listDocuments: async () => ({ memories: [] }),
		getDocument: async (id) => ({ id, content: "document body" }),
		listSpaces: async () => [{ containerTag: "personal" }],
	}
}

describe("local Supermemory MCP", () => {
	test("initializes and recalls personal plus project memory over Streamable HTTP", async () => {
		const server = createGatewayServer(
			{
				host: "127.0.0.1",
				port: 0,
				engineUrl: "http://127.0.0.1:1",
				apiKey: "gateway-secret",
				personalContainer: "personal",
			},
			backend(),
		)
		const baseUrl = await listen(server)
		cleanup.push(() => closeServer(server))

		const transport = new StreamableHTTPClientTransport(new URL(`${baseUrl}/mcp`), {
			requestInit: { headers: { Authorization: "Bearer gateway-secret" } },
		})
		const client = new Client({ name: "gateway-test", version: "1.0.0" })
		cleanup.push(() => client.close())
		await client.connect(transport)

		const tools = await client.listTools()
		expect(tools.tools.map((tool) => tool.name)).toEqual([
			"search_memory",
			"add_memory",
			"list_memories",
			"list_documents",
			"get_document",
			"list_spaces",
			"who_am_i",
		])

		const response = await client.callTool({
			name: "search_memory",
			arguments: { query: "deployment", projectContainer: "repo_ram0__abc" },
		})
		expect(response.structuredContent).toMatchObject({
			containers: ["personal", "repo_ram0__abc"],
			results: [
				{ id: "repo_ram0__abc-result", score: 0.9 },
				{ id: "personal-result", score: 0.7 },
			],
		})
	})

	test("supports on-demand save and explicit forget actions", async () => {
		const server = createGatewayServer(
			{ host: "127.0.0.1", port: 0, engineUrl: "http://127.0.0.1:1", apiKey: "gateway-secret", personalContainer: "personal" },
			backend(),
		)
		const baseUrl = await listen(server)
		cleanup.push(() => closeServer(server))
		const client = new Client({ name: "gateway-test", version: "1.0.0" })
		cleanup.push(() => client.close())
		await client.connect(
			new StreamableHTTPClientTransport(new URL(`${baseUrl}/mcp`), {
				requestInit: { headers: { Authorization: "Bearer gateway-secret" } },
			}),
		)

		expect(
			(await client.callTool({ name: "add_memory", arguments: { content: "Keep this", action: "save" } })).structuredContent,
		).toMatchObject({ action: "save", id: "created-document", containerTag: "personal" })
		expect(
			(await client.callTool({ name: "add_memory", arguments: { content: "Remove this", action: "forget" } })).structuredContent,
		).toMatchObject({ action: "forget", id: "forgotten-memory", success: true, containerTag: "personal" })
	})
})
