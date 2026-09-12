// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { execFileSync, spawnSync } from "node:child_process"
import {
	mkdtempSync,
	writeFileSync,
	mkdirSync,
	readFileSync,
	existsSync,
	rmSync,
} from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import { expect, test } from "vitest"

const sensitive = [
	"tools/ram0-migration/fixtures/credentials.env",
	".env.production",
	"nested/.env.staging",
	"credentials.env",
	"deploy/supermemory.env",
	"deploy/supermemory-runtime.env",
	"runtime.env",
	"exports.json",
	"migration/final-exports.json",
	"migration/import-plan.json",
	"migration/import-journal.jsonl",
	"migration/write-freeze.json",
	".worktrees/other/source.ts",
	".superpowers/private-review.md",
]
const allowed = [
	"README.md",
	"deploy/.env.example",
	"nested/.env.test.example",
	"tools/ram0-migration/fixtures/exports.json",
	"tools/ram0-migration/fixtures/mapping.json",
]

test("Git excludes private artifacts while keeping explicit examples and synthetic fixtures", () => {
	const path = mkdtempSync(join(tmpdir(), "ram0-git-ignore-"))
	try {
		execFileSync("git", ["init", "-q", path])
		writeFileSync(join(path, ".gitignore"), readFileSync(".gitignore"))
		for (const filename of sensitive)
			expect(
				spawnSync("git", ["check-ignore", "--no-index", "-q", filename], {
					cwd: path,
				}).status,
				filename,
			).toBe(0)
		for (const filename of allowed)
			expect(
				spawnSync("git", ["check-ignore", "--no-index", "-q", filename], {
					cwd: path,
				}).status,
				filename,
			).toBe(1)
	} finally {
		rmSync(path, { recursive: true, force: true })
	}
})

test("actual Docker COPY context excludes private artifacts and retains examples/fixtures", () => {
	const path = mkdtempSync(join(tmpdir(), "ram0-context-"))
	const context = join(path, "context")
	const output = join(path, "output")
	try {
		mkdirSync(context)
		writeFileSync(join(context, ".dockerignore"), readFileSync(".dockerignore"))
		writeFileSync(join(context, "Dockerfile"), "FROM scratch\nCOPY . /\n")
		for (const filename of [...sensitive, ...allowed]) {
			mkdirSync(dirname(join(context, filename)), { recursive: true })
			writeFileSync(
				join(context, filename),
				"synthetic non-secret test content\n",
			)
		}
		const built = spawnSync(
			"docker",
			[
				"buildx",
				"build",
				"--network=none",
				"--no-cache",
				"--output",
				`type=local,dest=${output}`,
				context,
			],
			{ encoding: "utf8", timeout: 60_000 },
		)
		expect(built.status, built.stderr).toBe(0)
		for (const filename of sensitive)
			expect(existsSync(join(output, filename)), filename).toBe(false)
		for (const filename of allowed)
			expect(existsSync(join(output, filename)), filename).toBe(true)
	} finally {
		rmSync(path, { recursive: true, force: true })
	}
}, 65_000)
