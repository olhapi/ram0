// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { describe, expect, test } from "vitest"

import { authenticateBearer } from "./auth"

describe("authenticateBearer", () => {
	test("accepts the configured bearer token", () => {
		expect(authenticateBearer("Bearer secret", "secret")).toBe(true)
	})

	test.each([undefined, "", "Basic secret", "Bearer wrong", "Bearer secret extra"])(
		"rejects a missing or invalid authorization header: %s",
		(header) => {
			expect(authenticateBearer(header, "secret")).toBe(false)
		},
	)
})
