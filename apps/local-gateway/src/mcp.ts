// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { z } from "zod"

import type { MemoryBackend } from "./backend"
import { recallAcrossScopes } from "./recall"
import { resolveContainers } from "./scope"

function textResult(structuredContent: Record<string, unknown>, text?: string) {
	return {
		content: [{ type: "text" as const, text: text ?? JSON.stringify(structuredContent) }],
		structuredContent,
	}
}

function errorResult(error: unknown) {
	const message = error instanceof Error ? error.message : "Unknown Supermemory error"
	return { isError: true, content: [{ type: "text" as const, text: message }] }
}

export function createMemoryMcpServer(backend: MemoryBackend, personalContainer: string): McpServer {
	const server = new McpServer(
		{ name: "ram0-supermemory", version: "0.1.0" },
		{ instructions: "Recall durable context with search_memory before guessing. Save durable facts only when useful later." },
	)

	server.registerTool(
		"search_memory",
		{
			description: "Search personal memory and, when supplied, the current repository memory. Use an explicit containerTag to search only one space.",
			inputSchema: {
				query: z.string().min(1).max(1000),
				projectContainer: z.string().min(1).max(100).optional(),
				containerTag: z.string().min(1).max(100).optional(),
				limit: z.number().int().min(1).max(50).default(10),
				includeProfile: z.boolean().default(true),
			},
			annotations: { readOnlyHint: true, openWorldHint: false },
		},
		async ({ query, projectContainer, containerTag, limit, includeProfile }) => {
			try {
				const containers = containerTag
					? [containerTag]
					: resolveContainers(projectContainer, personalContainer)
				const result = await recallAcrossScopes(backend, query, containers, { limit, includeProfile })
				const lines = result.results.map((hit) => `- [${Math.round(hit.score * 100)}% · ${hit.containerTag}] ${hit.text}`)
				return textResult({ ...result }, lines.length > 0 ? lines.join("\n") : "No matching memories found.")
			} catch (error) {
				return errorResult(error)
			}
		},
	)

	server.registerTool(
		"add_memory",
		{
			description: "Save a durable fact or explicitly forget an exact memory in a selected Supermemory space.",
			inputSchema: {
				content: z.string().min(1).max(200_000),
				action: z.enum(["save", "forget"]).default("save"),
				containerTag: z.string().min(1).max(100).optional(),
				projectContainer: z.string().min(1).max(100).optional(),
				scope: z.enum(["personal", "project"]).optional(),
			},
			annotations: { readOnlyHint: false, destructiveHint: true, openWorldHint: false },
		},
		async ({ content, action, containerTag, projectContainer, scope }) => {
			try {
				if (scope === "project" && !projectContainer && !containerTag) {
					throw new Error("projectContainer is required when scope is project")
				}
				const target = containerTag ?? (scope === "project" || (!scope && projectContainer) ? projectContainer : personalContainer)
				if (!target) throw new Error("A target container is required")
				if (action === "forget") {
					const result = await backend.forget(content, target)
					return textResult({ action, containerTag: target, ...result })
				}
				const result = await backend.add(content, target, { sm_source: "ram0-supermemory-mcp" })
				return textResult({ action, containerTag: target, id: result.id, status: "queued" })
			} catch (error) {
				return errorResult(error)
			}
		},
	)

	server.registerTool(
		"list_memories",
		{
			description: "List current extracted memory entries in one space.",
			inputSchema: {
				containerTag: z.string().min(1).max(100).optional(),
				limit: z.number().int().min(1).max(100).default(20),
			},
			annotations: { readOnlyHint: true, openWorldHint: false },
		},
		async ({ containerTag, limit }) => {
			try {
				const target = containerTag ?? personalContainer
				return textResult({ containerTag: target, data: await backend.listMemories(target, limit) })
			} catch (error) {
				return errorResult(error)
			}
		},
	)

	server.registerTool(
		"list_documents",
		{
			description: "List source documents in one space.",
			inputSchema: {
				containerTag: z.string().min(1).max(100).optional(),
				limit: z.number().int().min(1).max(100).default(20),
			},
			annotations: { readOnlyHint: true, openWorldHint: false },
		},
		async ({ containerTag, limit }) => {
			try {
				const target = containerTag ?? personalContainer
				return textResult({ containerTag: target, data: await backend.listDocuments(target, limit) })
			} catch (error) {
				return errorResult(error)
			}
		},
	)

	server.registerTool(
		"get_document",
		{
			description: "Read one source document by its stable ID.",
			inputSchema: { id: z.string().min(1).max(200) },
			annotations: { readOnlyHint: true, openWorldHint: false },
		},
		async ({ id }) => {
			try {
				return textResult({ id, document: await backend.getDocument(id) })
			} catch (error) {
				return errorResult(error)
			}
		},
	)

	server.registerTool(
		"list_spaces",
		{
			description: "List available Supermemory container spaces.",
			annotations: { readOnlyHint: true, openWorldHint: false },
		},
		async () => {
			try {
				return textResult({ spaces: await backend.listSpaces() })
			} catch (error) {
				return errorResult(error)
			}
		},
	)

	server.registerTool(
		"who_am_i",
		{
			description: "Report the local identity mode and default memory scope without exposing credentials.",
			annotations: { readOnlyHint: true, openWorldHint: false },
		},
		async () => textResult({ identity: "local-single-user", personalContainer }),
	)

	server.registerResource(
		"profile",
		"supermemory://profile",
		{ mimeType: "application/json", description: "The current personal Supermemory profile." },
		async (uri) => ({
			contents: [{ uri: uri.href, mimeType: "application/json", text: JSON.stringify(await backend.profile("", personalContainer)) }],
		}),
	)
	server.registerResource(
		"spaces",
		"supermemory://spaces",
		{ mimeType: "application/json", description: "Available local Supermemory spaces." },
		async (uri) => ({
			contents: [{ uri: uri.href, mimeType: "application/json", text: JSON.stringify(await backend.listSpaces()) }],
		}),
	)
	server.registerPrompt(
		"context",
		{
			description: "Guide an agent to recall personal and current-project context before work.",
			argsSchema: {
				query: z.string().min(1).max(1000),
				projectContainer: z.string().min(1).max(100).optional(),
			},
		},
		async ({ query, projectContainer }) => ({
			messages: [
				{
					role: "user" as const,
					content: {
						type: "text" as const,
						text: `Call search_memory with query ${JSON.stringify(query)}${projectContainer ? ` and projectContainer ${JSON.stringify(projectContainer)}` : ""}. Use only relevant durable context.`,
					},
				},
			],
		}),
	)

	return server
}
