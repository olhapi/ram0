// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

export type ImportMetadataValue = string | number | boolean | string[]
export type ImportMetadata = Record<string, ImportMetadataValue>

export interface ImportRecord {
	key: string
	content: string
	containerTag: string
	metadata: ImportMetadata
	isStatic?: boolean
	forgetAfter?: string | null
}

export interface InvalidRecord {
	accountId: string
	memoryId: string
	reason: string
}

export interface ImportPlan {
	version: 1
	sourceCount: number
	exactDuplicates: number
	invalid: InvalidRecord[]
	records: ImportRecord[]
}

export interface ContainerMapping {
	personalContainer: string
	appIds: Record<string, string>
}

export interface JournalEntry {
	containerTag: string
	keys: string[]
	targetIds: string[]
	completedAt: string
}

export interface ImportJournal {
	completedKeys: Set<string>
	append(entry: JournalEntry): Promise<void>
}

export interface MemoryImportBackend {
	listMemories(
		containerTag: string,
	): Promise<import("./verify").DestinationMemory[]>
	createMemories(
		containerTag: string,
		records: ImportRecord[],
	): Promise<Array<{ key: string; id: string }>>
}

export interface ImportSummary {
	imported: number
	skipped: number
	failed: Array<{ keys: string[]; reason: string }>
}
