// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { spawnSync } from "node:child_process"
import { createHash } from "node:crypto"
import { mkdtempSync, writeFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"
import { afterEach, expect, test } from "vitest"

const cleanup: string[] = []
afterEach(() => {
	for (const path of cleanup.splice(0))
		rmSync(path, { recursive: true, force: true })
})

function fixture() {
	const path = mkdtempSync(join(tmpdir(), "ram0-freeze-"))
	cleanup.push(path)
	const hash = createHash("sha256").update("[]\n").digest("hex")
	writeFileSync(join(path, "final-exports.json"), "[]\n")
	writeFileSync(join(path, "import-plan.json"), "[]\n")
	writeFileSync(
		join(path, "write-freeze.json"),
		JSON.stringify({
			version: 1,
			otherWritersStopped: true,
			exportSha256: hash,
			planSha256: hash,
			apiFinishedAt: "2026-09-12T00:00:00Z",
			dashboardFinishedAt: "2026-09-12T00:00:00Z",
		}),
		{ mode: 0o600 },
	)
	return path
}

function run(
	path: string,
	command: string,
	running = "false",
	finished = "2026-09-12T00:00:00Z",
) {
	return spawnSync(
		"bash",
		[
			"-c",
			`source "$1"
candidate_compose() { echo MUTATION; return 90; }
live_compose() { echo MUTATION; return 90; }
test_running=$3
test_finished=$4
docker() { printf '{"Running":%s,"FinishedAt":"%s"}\\n' "$test_running" "$test_finished"; }
RAM0_MIGRATION_DIR=$2
${command}`,
			"test",
			resolve("deploy/supermemory/deploy-unraid.sh"),
			path,
			running,
			finished,
		],
		{ encoding: "utf8" },
	)
}

for (const operation of ["import_memories", "promote"]) {
	test(`${operation} refuses mutations without the final frozen export`, () => {
		const path = mkdtempSync(join(tmpdir(), "ram0-freeze-"))
		cleanup.push(path)
		const result = run(path, operation)
		expect(result.status).not.toBe(0)
		expect(result.stdout).not.toContain("MUTATION")
	})
}

test("freeze validation binds stopped writers and final export/plan hashes", () => {
	const path = fixture()
	expect(run(path, "validate_write_freeze").status).toBe(0)
	expect(run(path, "validate_write_freeze", "true").status).not.toBe(0)
	expect(
		run(path, "validate_write_freeze", "false", "2026-09-12T01:00:00Z").status,
	).not.toBe(0)
	writeFileSync(join(path, "import-plan.json"), "[1]\n")
	expect(run(path, "validate_write_freeze").status).not.toBe(0)
})
