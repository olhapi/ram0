// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

export interface MemoryHit {
	id: string
	text: string
	score: number
	containerTag: string
	kind: "memory" | "chunk"
	title?: string
	metadata?: Record<string, unknown> | null
}

export interface Profile {
	static: string[]
	dynamic: string[]
}

export interface MemoryBackend {
	search(query: string, containerTag: string, limit: number): Promise<MemoryHit[]>
	profile(query: string, containerTag: string): Promise<Profile>
	add(content: string, containerTag: string, metadata?: Record<string, unknown>): Promise<{ id: string }>
	forget(content: string, containerTag: string): Promise<{ id?: string; success: boolean }>
	listMemories(containerTag: string, limit: number): Promise<unknown>
	listDocuments(containerTag: string, limit: number): Promise<unknown>
	getDocument(id: string): Promise<unknown>
	listSpaces(): Promise<unknown>
}

export class SupermemoryBackend implements MemoryBackend {
	private readonly baseUrl: string

	constructor(baseUrl: string, private readonly apiKey: string) {
		this.baseUrl = baseUrl.replace(/\/$/, "")
	}

	private async request(path: string, init: RequestInit = {}): Promise<unknown> {
		const response = await fetch(`${this.baseUrl}${path}`, {
			...init,
			headers: {
				Authorization: `Bearer ${this.apiKey}`,
				...(init.body ? { "content-type": "application/json" } : {}),
				...init.headers,
			},
			signal: init.signal ?? AbortSignal.timeout(30_000),
		})
		if (!response.ok) {
			throw new Error(`Supermemory API request failed with HTTP ${response.status}`)
		}
		return response.json()
	}

	private post(path: string, body: unknown, method = "POST"): Promise<unknown> {
		return this.request(path, { method, body: JSON.stringify(body) })
	}

	async search(query: string, containerTag: string, limit: number): Promise<MemoryHit[]> {
		const value = (await this.post("/v4/search", {
			q: query,
			containerTag,
			limit,
			searchMode: "hybrid",
		})) as { results?: unknown[] }

		if (!Array.isArray(value.results)) {
			throw new Error("Supermemory search returned an invalid response")
		}

		return value.results.map((raw) => {
			if (!raw || typeof raw !== "object") throw new Error("Supermemory search returned an invalid result")
			const result = raw as Record<string, unknown>
			const text = typeof result.memory === "string" ? result.memory : result.chunk
			if (typeof result.id !== "string" || typeof text !== "string" || typeof result.similarity !== "number") {
				throw new Error("Supermemory search returned an invalid result")
			}
			return {
				id: result.id,
				text,
				score: result.similarity,
				containerTag,
				kind: typeof result.memory === "string" ? "memory" : "chunk",
				...(typeof result.title === "string" ? { title: result.title } : {}),
				...(result.metadata && typeof result.metadata === "object"
					? { metadata: result.metadata as Record<string, unknown> }
					: {}),
			}
		})
	}

	async profile(query: string, containerTag: string): Promise<Profile> {
		const value = (await this.post("/v4/profile", { q: query, containerTag })) as {
			profile?: { static?: unknown; dynamic?: unknown }
		}
		if (!Array.isArray(value.profile?.static) || !Array.isArray(value.profile.dynamic)) {
			throw new Error("Supermemory profile returned an invalid response")
		}
		return {
			static: value.profile.static.filter((item): item is string => typeof item === "string"),
			dynamic: value.profile.dynamic.filter((item): item is string => typeof item === "string"),
		}
	}

	async add(content: string, containerTag: string, metadata?: Record<string, unknown>): Promise<{ id: string }> {
		const value = (await this.post("/v3/documents", { content, containerTag, ...(metadata ? { metadata } : {}) })) as {
			id?: unknown
		}
		if (typeof value.id !== "string") throw new Error("Supermemory add returned an invalid response")
		return { id: value.id }
	}

	async forget(content: string, containerTag: string): Promise<{ id?: string; success: boolean }> {
		const value = (await this.post("/v4/memories", { content, containerTag }, "DELETE")) as {
			id?: unknown
			forgotten?: unknown
		}
		return {
			...(typeof value.id === "string" ? { id: value.id } : {}),
			success: value.forgotten === true,
		}
	}

	listMemories(containerTag: string, limit: number): Promise<unknown> {
		return this.post("/v4/memories/list", {
			containerTags: [containerTag],
			page: 1,
			limit,
			sort: "createdAt",
			order: "desc",
		})
	}

	listDocuments(containerTag: string, limit: number): Promise<unknown> {
		return this.post("/v3/documents/list", {
			containerTags: [containerTag],
			page: 1,
			limit,
			sort: "createdAt",
			order: "desc",
			includeContent: false,
		})
	}

	getDocument(id: string): Promise<unknown> {
		return this.request(`/v3/documents/${encodeURIComponent(id)}`)
	}

	listSpaces(): Promise<unknown> {
		return this.request("/v3/container-tags/list")
	}
}
