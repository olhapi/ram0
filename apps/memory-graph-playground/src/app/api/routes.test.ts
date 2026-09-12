// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { afterEach, describe, expect, test } from "vitest"

import { POST as listContainerTags } from "./container-tags/route"
import { POST as loadGraph } from "./graph/route"

const originalFetch = globalThis.fetch

afterEach(() => {
	globalThis.fetch = originalFetch
	delete process.env.SUPERMEMORY_API_BASE_URL
})

describe("memory graph API routes", () => {
	test("loads graph data from the configured local Supermemory origin", async () => {
		process.env.SUPERMEMORY_API_BASE_URL = "http://supermemory-engine:6767"
		let capturedUrl: URL | undefined
		globalThis.fetch = (async (input: URL | RequestInfo) => {
			capturedUrl = new URL(input.toString())
			return Response.json({ documents: [] })
		}) as typeof fetch

		const response = await loadGraph(
			new Request("http://graph.local/api/graph", {
				method: "POST",
				body: JSON.stringify({ apiKey: "test-key" }),
			}),
		)

		expect(response.status).toBe(200)
		expect(capturedUrl?.origin).toBe("http://supermemory-engine:6767")
		expect(capturedUrl?.pathname).toBe("/v3/documents/documents")
	})

	test("loads spaces from the configured local Supermemory origin", async () => {
		process.env.SUPERMEMORY_API_BASE_URL = "http://supermemory-engine:6767"
		let capturedUrl: URL | undefined
		globalThis.fetch = (async (input: URL | RequestInfo) => {
			capturedUrl = new URL(input.toString())
			return Response.json([])
		}) as typeof fetch

		const response = await listContainerTags(
			new Request("http://graph.local/api/container-tags", {
				method: "POST",
				body: JSON.stringify({ apiKey: "test-key" }),
			}),
		)

		expect(response.status).toBe(200)
		expect(capturedUrl?.origin).toBe("http://supermemory-engine:6767")
		expect(capturedUrl?.pathname).toBe("/v3/container-tags/list")
	})
})
