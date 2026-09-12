// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import type { IncomingMessage, ServerResponse } from "node:http"
import { Readable } from "node:stream"

const HOP_BY_HOP_HEADERS = new Set(["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"])

export async function proxyRequest(request: IncomingMessage, response: ServerResponse, engineUrl: string): Promise<void> {
	const headers = new Headers()
	for (const [name, value] of Object.entries(request.headers)) {
		if (HOP_BY_HOP_HEADERS.has(name) || name === "host" || value === undefined) continue
		if (Array.isArray(value)) for (const item of value) headers.append(name, item)
		else headers.set(name, value)
	}
	const method = request.method ?? "GET"
	const hasBody = method !== "GET" && method !== "HEAD"
	const controller = new AbortController()
	request.once("aborted", () => controller.abort())
	const init: RequestInit & { duplex?: "half" } = {
		method,
		headers,
		signal: controller.signal,
		...(hasBody ? { body: Readable.toWeb(request) as ReadableStream, duplex: "half" } : {}),
	}
	const upstream = await fetch(`${engineUrl.replace(/\/$/, "")}${request.url ?? "/"}`, init)
	response.statusCode = upstream.status
	for (const [name, value] of upstream.headers) {
		if (!HOP_BY_HOP_HEADERS.has(name)) response.setHeader(name, value)
	}
	if (!upstream.body) {
		response.end()
		return
	}
	await new Promise<void>((resolve, reject) => {
		Readable.fromWeb(upstream.body as never)
			.once("error", reject)
			.pipe(response)
			.once("finish", resolve)
			.once("error", reject)
	})
}
