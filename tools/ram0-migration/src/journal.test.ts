// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { mkdtemp, readFile, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { describe, expect, test } from "vitest"

import { FileImportJournal } from "./journal"

describe("FileImportJournal", () => {
	test("persists completed keys as append-only JSONL and reloads them", async () => {
		const directory = await mkdtemp(join(tmpdir(), "ram0-journal-"))
		const path = join(directory, "journal.jsonl")
		try {
			const journal = await FileImportJournal.open(path)
			await journal.append({
				containerTag: "personal",
				keys: ["key-1", "key-2"],
				targetIds: ["target-1", "target-2"],
				completedAt: "2026-09-12T00:00:00.000Z",
			})

			const reopened = await FileImportJournal.open(path)
			expect([...reopened.completedKeys].sort()).toEqual(["key-1", "key-2"])
			expect((await readFile(path, "utf8")).trim().split("\n")).toHaveLength(1)
		} finally {
			await rm(directory, { recursive: true, force: true })
		}
	})
})
