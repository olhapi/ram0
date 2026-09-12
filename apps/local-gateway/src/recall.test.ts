// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { describe, expect, test } from "vitest"

import type { MemoryBackend, MemoryHit } from "./backend"
import { recallAcrossScopes } from "./recall"

function fakeBackend(results: Record<string, MemoryHit[]>): MemoryBackend {
	return {
		search: async (_query, container) => results[container] ?? [],
		profile: async (_query, container) =>
			container === "personal" ? { static: ["Prefers concise output"], dynamic: [] } : { static: [], dynamic: [] },
		add: async () => ({ id: "unused" }),
		forget: async () => ({ success: false }),
		listMemories: async () => ({}),
		listDocuments: async () => ({}),
		getDocument: async () => ({}),
		listSpaces: async () => [],
	}
}

describe("recallAcrossScopes", () => {
	test("merges scopes by score and deduplicates IDs and normalized text", async () => {
		const backend = fakeBackend({
			personal: [
				{ id: "shared", text: "Duplicate by ID", score: 0.4, containerTag: "personal", kind: "memory" },
				{ id: "personal-medium", text: "  Same   remembered fact ", score: 0.6, containerTag: "personal", kind: "memory" },
			],
			repo_ram0__abc: [
				{ id: "project-high", text: "Project deployment", score: 0.9, containerTag: "repo_ram0__abc", kind: "memory" },
				{ id: "shared", text: "Duplicate by ID", score: 0.8, containerTag: "repo_ram0__abc", kind: "memory" },
				{ id: "text-duplicate", text: "same remembered FACT", score: 0.5, containerTag: "repo_ram0__abc", kind: "memory" },
			],
		})

		const result = await recallAcrossScopes(backend, "deployment", ["personal", "repo_ram0__abc"], {
			limit: 3,
			includeProfile: true,
		})

		expect(result.results.map((item) => item.id)).toEqual(["project-high", "shared", "personal-medium"])
		expect(result.containers).toEqual(["personal", "repo_ram0__abc"])
		expect(result.profiles.personal.static).toContain("Prefers concise output")
	})

	test("does not request profiles when the caller opts out", async () => {
		let profileCalls = 0
		const backend = fakeBackend({ personal: [] })
		backend.profile = async () => {
			profileCalls += 1
			return { static: [], dynamic: [] }
		}

		const result = await recallAcrossScopes(backend, "anything", ["personal"], { limit: 5, includeProfile: false })

		expect(result.profiles).toEqual({})
		expect(profileCalls).toBe(0)
	})
})
