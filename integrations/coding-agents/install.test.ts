// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { mkdtempSync, readFileSync, rmSync, statSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { afterEach, describe, expect, test } from "vitest"

import { installAgentIntegrations } from "./install.mjs"

const cleanup: string[] = []

afterEach(() => {
	for (const path of cleanup.splice(0)) rmSync(path, { recursive: true, force: true })
})

describe("coding-agent installer", () => {
	test("installs self-hosted hooks and MCP once without persisting the key", async () => {
		const home = mkdtempSync(join(tmpdir(), "ram0-agent-install-"))
		cleanup.push(home)
		const calls: string[][] = []
		const runner = async (command: string, args: string[]) => {
			calls.push([command, ...args])
			return { status: 0, output: "" }
		}

		const first = await installAgentIntegrations({ home, baseUrl: "https://brain.example.test/", runner })
		const firstCalls = calls.map((call) => [...call])
		const environment = readFileSync(first.environmentFile, "utf8")
		const marker = readFileSync(first.markerFile, "utf8")
		const second = await installAgentIntegrations({ home, baseUrl: "https://brain.example.test/", runner })

		expect(second.changed).toBe(false)
		expect(calls).toEqual(firstCalls)
		expect(environment).toContain("SUPERMEMORY_MCP_URL='https://brain.example.test/mcp'")
		expect(environment).toContain('SUPERMEMORY_CODEX_API_KEY="$SUPERMEMORY_API_KEY"')
		expect(environment).toContain('SUPERMEMORY_CC_API_KEY="$SUPERMEMORY_API_KEY"')
		expect(environment).not.toContain("replace-me")
		expect(marker).not.toContain("SUPERMEMORY_API_KEY")
		expect(statSync(first.environmentFile).mode & 0o777).toBe(0o600)
		expect(firstCalls.some((call) => call.join(" ").includes("--bearer-token-env-var SUPERMEMORY_API_KEY"))).toBe(true)
	})

	test("rejects URLs that could persist credentials", async () => {
		await expect(
			installAgentIntegrations({
				home: "/tmp/unused",
				baseUrl: "https://user:secret@example.test",
				runner: async () => ({ status: 0, output: "" }),
			}),
		).rejects.toThrow("credentials")
	})
})
