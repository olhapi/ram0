// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { execFileSync } from "node:child_process"
import { mkdtempSync, mkdirSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { afterEach, describe, expect, test } from "vitest"

import {
	normalizeGitRemote,
	resolveRepositoryContainer,
} from "./project-scope.mjs"

const cleanup: string[] = []

afterEach(() => {
	for (const path of cleanup.splice(0))
		rmSync(path, { recursive: true, force: true })
})

function repository(parent: string, name: string, remote: string): string {
	const path = join(parent, name)
	mkdirSync(path)
	execFileSync("git", ["init", "-q"], { cwd: path })
	execFileSync("git", ["remote", "add", "origin", remote], { cwd: path })
	return path
}

describe("shared coding-agent repository scope", () => {
	test("shares the common-directory identity for remote-less linked worktrees", () => {
		const root = mkdtempSync(join(tmpdir(), "ram0-scope-"))
		cleanup.push(root)
		const main = repository(root, "main", "https://example.test/temp.git")
		execFileSync("git", ["remote", "remove", "origin"], { cwd: main })
		execFileSync(
			"git",
			[
				"-c",
				"user.name=Test",
				"-c",
				"user.email=test@example.invalid",
				"commit",
				"--allow-empty",
				"-qm",
				"test",
			],
			{ cwd: main },
		)
		const linked = join(root, "linked")
		execFileSync("git", ["worktree", "add", "-q", "--detach", linked], {
			cwd: main,
		})
		expect(resolveRepositoryContainer(linked)).toBe(
			resolveRepositoryContainer(main),
		)
	})
	test("normalizes HTTPS and SSH remotes to one identity", () => {
		expect(normalizeGitRemote("git@GitHub.com:SupermemoryAI/Repo.git")).toBe(
			"github.com/supermemoryai/repo",
		)
		expect(
			normalizeGitRemote("https://github.com/supermemoryai/repo.git"),
		).toBe("github.com/supermemoryai/repo")
	})

	test("shares a tag across clones while separating same-named repositories", () => {
		const root = mkdtempSync(join(tmpdir(), "ram0-scope-"))
		cleanup.push(root)
		const cloneA = repository(root, "clone-a", "git@github.com:olhapi/ram0.git")
		const cloneB = repository(
			root,
			"clone-b",
			"https://github.com/olhapi/ram0.git",
		)
		const other = repository(
			root,
			"same-name",
			"https://git.example.com/olhapi/ram0.git",
		)

		expect(resolveRepositoryContainer(cloneA)).toBe(
			resolveRepositoryContainer(cloneB),
		)
		expect(resolveRepositoryContainer(other)).not.toBe(
			resolveRepositoryContainer(cloneA),
		)
		expect(resolveRepositoryContainer(cloneA)).toMatch(
			/^repo_ram0__[0-9a-f]{16}$/,
		)
	})
})
