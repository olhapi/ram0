// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { expect, test } from "vitest"

import { requeueFailedDocuments } from "../requeue-failed-documents.mjs"

const documents = [
	{ id: "done", status: "done", customId: "a", containerTags: ["personal"] },
	{
		id: "session",
		status: "failed",
		customId: "s1",
		containerTags: ["repo_x"],
	},
	{
		id: "import",
		status: "failed",
		customId: null,
		containerTags: ["personal"],
	},
]

function fakeApi() {
	const posts: unknown[] = []
	const fetchImpl = async (url: string, init: RequestInit = {}) => {
		const path = new URL(url).pathname
		let body: unknown
		if (path === "/v3/documents/list") {
			const { page } = JSON.parse(String(init.body))
			body = {
				memories: page === 1 ? documents.slice(0, 2) : documents.slice(2),
				pagination: { currentPage: page, totalPages: 2 },
			}
		} else if (path === "/v3/documents/session") {
			body = { content: "turn text", metadata: { type: "session_turn" } }
		} else if (path === "/v3/documents" && init.method === "POST") {
			posts.push(JSON.parse(String(init.body)))
			body = { id: "session", status: "queued" }
		} else {
			return new Response("{}", { status: 404 })
		}
		return new Response(JSON.stringify(body), { status: 200 })
	}
	return { posts, fetchImpl: fetchImpl as typeof fetch }
}

test("dry run reports failed documents without writing", async () => {
	const { posts, fetchImpl } = fakeApi()
	const summary = await requeueFailedDocuments({
		baseUrl: "https://brain.example.test/",
		apiKey: "test-only",
		fetchImpl,
		log: () => {},
	})
	expect(summary).toEqual({ failed: 2, requeued: 0, skipped: 1 })
	expect(posts).toEqual([])
})

test("apply re-posts failed documents under their customId only", async () => {
	const { posts, fetchImpl } = fakeApi()
	const summary = await requeueFailedDocuments({
		baseUrl: "https://brain.example.test",
		apiKey: "test-only",
		apply: true,
		fetchImpl,
		log: () => {},
	})
	expect(summary).toEqual({ failed: 2, requeued: 1, skipped: 1 })
	expect(posts).toEqual([
		{
			content: "turn text",
			containerTag: "repo_x",
			customId: "s1",
			metadata: { type: "session_turn" },
		},
	])
})
