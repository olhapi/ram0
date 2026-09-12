// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { describe, expect, test } from "vitest"

import { loadConfig } from "./config"

describe("loadConfig", () => {
	test("rejects a missing API key instead of starting unauthenticated", () => {
		expect(() => loadConfig({})).toThrow("SUPERMEMORY_API_KEY")
	})

	test("loads explicit values and normalizes the engine URL", () => {
		expect(
			loadConfig({
				SUPERMEMORY_API_KEY: "secret",
				GATEWAY_HOST: "127.0.0.1",
				GATEWAY_PORT: "18888",
				SUPERMEMORY_ENGINE_URL: "http://engine:6767/",
				SUPERMEMORY_PERSONAL_CONTAINER: "personal_oleh",
			}),
		).toEqual({
			host: "127.0.0.1",
			port: 18888,
			engineUrl: "http://engine:6767",
			apiKey: "secret",
			personalContainer: "personal_oleh",
		})
	})

	test("rejects an invalid TCP port", () => {
		expect(() =>
			loadConfig({
				SUPERMEMORY_API_KEY: "secret",
				GATEWAY_PORT: "70000",
			}),
		).toThrow("GATEWAY_PORT")
	})
})
