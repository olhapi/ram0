// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http"
import { pathToFileURL } from "node:url"
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js"

import { authenticateBearer } from "./auth"
import { SupermemoryBackend, type MemoryBackend } from "./backend"
import { loadConfig, type GatewayConfig } from "./config"
import { createMemoryMcpServer } from "./mcp"
import { proxyRequest } from "./proxy"

const MAX_MCP_BODY_BYTES = 1024 * 1024

async function readJsonBody(request: IncomingMessage): Promise<unknown> {
	const chunks: Buffer[] = []
	let size = 0
	for await (const chunk of request) {
		const buffer = Buffer.from(chunk)
		size += buffer.byteLength
		if (size > MAX_MCP_BODY_BYTES) throw new Error("MCP request body exceeds 1 MiB")
		chunks.push(buffer)
	}
	if (chunks.length === 0) return undefined
	return JSON.parse(Buffer.concat(chunks).toString("utf8"))
}

function sendJson(response: ServerResponse, status: number, value: unknown, extraHeaders: Record<string, string> = {}): void {
	response.writeHead(status, { "content-type": "application/json", ...extraHeaders })
	response.end(JSON.stringify(value))
}

async function handleMcp(request: IncomingMessage, response: ServerResponse, config: GatewayConfig, backend: MemoryBackend): Promise<void> {
	if (!authenticateBearer(request.headers.authorization, config.apiKey)) {
		sendJson(response, 401, { error: "unauthorized" }, { "www-authenticate": "Bearer" })
		return
	}
	if (!["GET", "POST", "DELETE"].includes(request.method ?? "")) {
		sendJson(response, 405, { error: "method not allowed" }, { allow: "GET, POST, DELETE" })
		return
	}

	const mcp = createMemoryMcpServer(backend, config.personalContainer)
	const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined })
	let closed = false
	const cleanup = async () => {
		if (closed) return
		closed = true
		await transport.close()
		await mcp.close()
	}
	response.once("close", () => void cleanup())
	try {
		const body = request.method === "POST" ? await readJsonBody(request) : undefined
		await mcp.connect(transport)
		await transport.handleRequest(request, response, body)
	} catch (error) {
		await cleanup()
		if (!response.headersSent) {
			const status = error instanceof Error && error.message.includes("1 MiB") ? 413 : 400
			sendJson(response, status, { error: status === 413 ? "request too large" : "invalid MCP request" })
		}
	}
}

export function createGatewayServer(config: GatewayConfig, backend: MemoryBackend): Server {
	return createServer(async (request, response) => {
		try {
			const path = new URL(request.url ?? "/", "http://gateway.invalid").pathname
			if (path === "/mcp") {
				await handleMcp(request, response, config, backend)
				return
			}
			if (path === "/health") {
				const engine = await fetch(`${config.engineUrl}/health`, {
					headers: { Authorization: `Bearer ${config.apiKey}` },
					signal: AbortSignal.timeout(3_000),
				})
				const ok = engine.ok
				sendJson(response, ok ? 200 : 503, { status: ok ? "ok" : "degraded", engine: ok ? "ok" : "unavailable" })
				return
			}
			await proxyRequest(request, response, config.engineUrl)
		} catch {
			if (!response.headersSent) sendJson(response, 502, { error: "upstream unavailable" })
			else response.destroy()
		}
	})
}

export function main(): void {
	const config = loadConfig(process.env)
	const backend = new SupermemoryBackend(config.engineUrl, config.apiKey)
	const server = createGatewayServer(config, backend)
	server.listen(config.port, config.host, () => {
		console.log(`Ram0 Supermemory gateway listening on ${config.host}:${config.port}`)
	})
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
	main()
}
