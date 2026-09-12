// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import type { MemoryBackend, MemoryHit, Profile } from "./backend"

export interface RecallResult {
	query: string
	containers: string[]
	results: MemoryHit[]
	profiles: Record<string, Profile>
}

export async function recallAcrossScopes(
	backend: MemoryBackend,
	query: string,
	containers: string[],
	options: { limit: number; includeProfile: boolean },
): Promise<RecallResult> {
	const searches = await Promise.all(containers.map((container) => backend.search(query, container, options.limit)))
	const candidates = searches.flat().sort((left, right) => right.score - left.score)
	const seenIds = new Set<string>()
	const seenTexts = new Set<string>()
	const results: MemoryHit[] = []

	for (const candidate of candidates) {
		const normalizedText = candidate.text.normalize("NFKC").trim().replace(/\s+/g, " ").toLocaleLowerCase("en-US")
		if (seenIds.has(candidate.id) || seenTexts.has(normalizedText)) continue
		seenIds.add(candidate.id)
		seenTexts.add(normalizedText)
		results.push(candidate)
		if (results.length === options.limit) break
	}

	const profiles: Record<string, Profile> = {}
	if (options.includeProfile) {
		const values = await Promise.all(containers.map((container) => backend.profile(query, container)))
		for (const [index, container] of containers.entries()) profiles[container] = values[index]
	}

	return { query, containers: [...containers], results, profiles }
}
