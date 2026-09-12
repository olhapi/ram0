// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { describe, expect, test } from "vitest"

import { resolveContainers } from "./scope"

describe("resolveContainers", () => {
	test("recalls personal memory before the explicit project scope", () => {
		expect(resolveContainers("repo_ram0__abc")).toEqual(["personal", "repo_ram0__abc"])
	})

	test("uses only personal memory when no project scope is supplied", () => {
		expect(resolveContainers()).toEqual(["personal"])
	})

	test("does not duplicate the personal scope", () => {
		expect(resolveContainers("personal")).toEqual(["personal"])
	})
})
