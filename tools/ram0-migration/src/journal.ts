// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { appendFile, chmod, readFile } from "node:fs/promises"

import type { ImportJournal, JournalEntry } from "./model"

function parseEntry(line: string): JournalEntry {
	const value = JSON.parse(line) as Partial<JournalEntry>
	if (
		typeof value.containerTag !== "string" ||
		!Array.isArray(value.keys) ||
		!value.keys.every((key) => typeof key === "string") ||
		!Array.isArray(value.targetIds) ||
		!value.targetIds.every((id) => typeof id === "string") ||
		value.keys.length !== value.targetIds.length ||
		typeof value.completedAt !== "string"
	) {
		throw new Error("Import journal contains an invalid entry")
	}
	return value as JournalEntry
}

export class FileImportJournal implements ImportJournal {
	readonly completedKeys = new Set<string>()

	private constructor(private readonly path: string) {}

	static async open(path: string): Promise<FileImportJournal> {
		const journal = new FileImportJournal(path)
		let content = ""
		try {
			content = await readFile(path, "utf8")
		} catch (error) {
			if (!(error && typeof error === "object" && Reflect.get(error, "code") === "ENOENT")) throw error
		}
		for (const line of content.split("\n").filter(Boolean)) {
			for (const key of parseEntry(line).keys) journal.completedKeys.add(key)
		}
		return journal
	}

	async append(entry: JournalEntry): Promise<void> {
		if (entry.keys.length !== entry.targetIds.length) throw new Error("Journal keys and target IDs must have equal length")
		await appendFile(this.path, `${JSON.stringify(entry)}\n`, { encoding: "utf8", mode: 0o600 })
		await chmod(this.path, 0o600)
		for (const key of entry.keys) this.completedKeys.add(key)
	}
}
