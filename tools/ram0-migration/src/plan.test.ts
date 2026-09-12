// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import fixture from "../fixtures/two-accounts.json"
import { describe, expect, test } from "vitest"

import { buildImportPlan } from "./plan"

describe("buildImportPlan", () => {
	test("exact-deduplicates two account exports while retaining all provenance", () => {
		const plan = buildImportPlan(fixture, {
			personalContainer: "personal",
			appIds: { "github.com-example-atlas": "repo_atlas__1234abcd" },
		})

		expect(plan.sourceCount).toBe(4)
		expect(plan.records).toHaveLength(3)
		expect(plan.exactDuplicates).toBe(1)
		expect(plan.invalid).toEqual([])
		const duplicate = plan.records.find((record) => record.content === "The user prefers concise output.")
		expect(duplicate?.metadata.ram0_source_ids).toEqual(["a-1", "b-7"])
		expect(duplicate?.metadata.ram0_account_ids).toEqual(["account-a", "account-b"])
	})

	test("reports empty and unmapped records instead of silently discarding them", () => {
		const plan = buildImportPlan(
			[
				{
					account: { id: "account-a" },
					memories: { results: [{ id: "empty", memory: "   " }, { id: "project", memory: "Keep me", app_id: "unknown" }] },
				},
			],
			{ personalContainer: "personal", appIds: {} },
		)

		expect(plan.records).toEqual([])
		expect(plan.invalid).toEqual([
			{ accountId: "account-a", memoryId: "empty", reason: "memory text is empty" },
			{ accountId: "account-a", memoryId: "project", reason: "unmapped app_id: unknown" },
		])
	})

	test("rejects content beyond the v4 direct-memory limit before import", () => {
		const plan = buildImportPlan(
			[{ account: { id: "account-a" }, memories: { results: [{ id: "too-long", memory: "x".repeat(10_001) }] } }],
			{ personalContainer: "personal", appIds: {} },
		)

		expect(plan.records).toEqual([])
		expect(plan.invalid).toEqual([
			{ accountId: "account-a", memoryId: "too-long", reason: "memory text exceeds 10000 characters" },
		])
	})
})
