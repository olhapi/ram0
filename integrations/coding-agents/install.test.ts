// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import {
	existsSync,
	mkdtempSync,
	readFileSync,
	rmSync,
	statSync,
	mkdirSync,
	writeFileSync,
} from "node:fs"
import { execFileSync, spawn } from "node:child_process"
import { createServer } from "node:http"
import type { AddressInfo } from "node:net"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { afterEach, describe, expect, test } from "vitest"

import { installAgentIntegrations } from "./install.mjs"

const cleanup: string[] = []

afterEach(() => {
	for (const path of cleanup.splice(0))
		rmSync(path, { recursive: true, force: true })
})

describe("coding-agent installer", () => {
	test("installed hooks recall only personal and the active repository for both clients", async () => {
		const home = mkdtempSync(join(tmpdir(), "ram0-hooks-"))
		cleanup.push(home)
		mkdirSync(join(home, ".claude"))
		mkdirSync(join(home, ".codex"))
		writeFileSync(
			join(home, ".codex", "hooks.json"),
			JSON.stringify({ hooks: {} }),
		)
		writeFileSync(
			join(home, ".claude", "settings.json"),
			JSON.stringify({
				permissions: { allow: ["Read"] },
				hooks: {
					Stop: [{ hooks: [{ type: "command", command: "existing-capture" }] }],
				},
			}),
		)
		const requests: Array<{
			path?: string
			authorization?: string
			body: { containerTag: string }
		}> = []
		const server = createServer(async (request, response) => {
			const chunks = []
			for await (const chunk of request) chunks.push(Buffer.from(chunk))
			const body = JSON.parse(Buffer.concat(chunks).toString())
			requests.push({
				path: request.url,
				authorization: request.headers.authorization,
				body,
			})
			response.end(
				JSON.stringify({
					profile: { static: [`context:${body.containerTag}`], dynamic: [] },
					searchResults: {
						results: [
							{ memory: `match:${body.containerTag}`, similarity: 0.9 },
							{ memory: `noise:${body.containerTag}`, similarity: 0.6 },
						],
					},
				}),
			)
		})
		await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
		try {
			const baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
			await installAgentIntegrations({
				home,
				baseUrl,
				runner: async () => ({ status: 0, output: "" }),
				apiKey: "synthetic-test-key",
			})
			const withoutShellEnv = Object.fromEntries(
				Object.entries(process.env).filter(
					([name]) => !name.startsWith("SUPERMEMORY_"),
				),
			)
			for (const [client, filename] of [
				[".codex", "hooks.json"],
				[".claude", "settings.json"],
			] as const) {
				const settings = JSON.parse(
					readFileSync(join(home, client, filename), "utf8"),
				) as {
					permissions: { allow: string[] }
					hooks: Record<string, Array<{ hooks: Array<{ command: string }> }>>
				}
				expect(settings.hooks.SessionStart).toEqual(expect.any(Array))
				if (client === ".claude")
					expect(settings.permissions.allow).toEqual(["Read"])
				for (const repo of ["alpha", "beta"]) {
					const cwd = join(home, `${client}-${repo}`)
					mkdirSync(cwd)
					execFileSync("git", ["init", "-q"], { cwd })
					execFileSync(
						"git",
						[
							"remote",
							"add",
							"origin",
							`https://example.test/team/${repo}.git`,
						],
						{ cwd },
					)
					for (const [event, shellEnv] of [
						["SessionStart", true],
						["UserPromptSubmit", false],
					] as const) {
						const command = settings.hooks[event]
							?.flatMap((group) => group.hooks)
							.find((hook) => hook.command.includes("recall.mjs"))?.command
						if (!command) throw new Error("Recall hook not installed")
						const before = requests.length
						const output = await new Promise<string>((resolve, reject) => {
							const child = spawn("bash", ["-c", command], {
								cwd,
								// GUI launchers skip shell profiles; the hook must fall back to installed files.
								env: shellEnv
									? {
											...process.env,
											SUPERMEMORY_API_URL: baseUrl,
											SUPERMEMORY_API_KEY: "synthetic-test-key",
										}
									: { ...withoutShellEnv, HOME: home },
							})
							let stdout = ""
							child.stdout.on("data", (chunk) => {
								stdout += chunk
							})
							child.on("error", reject)
							child.on("close", (code) =>
								code === 0
									? resolve(stdout)
									: reject(new Error(`hook exit ${code}`)),
							)
							child.stdin.end(
								JSON.stringify({
									hook_event_name: event,
									cwd,
									prompt: "decision <private>hidden-value</private>",
								}),
							)
						})
						const current = requests.slice(before)
						expect(current).toHaveLength(2)
						expect(current.map((item) => item.body.containerTag)).toEqual([
							"personal",
							expect.stringMatching(new RegExp(`^repo_${repo}__[0-9a-f]{16}$`)),
						])
						expect(
							current.every(
								(item) =>
									item.path === "/v4/profile" &&
									item.authorization === "Bearer synthetic-test-key",
							),
						).toBe(true)
						expect(JSON.stringify(current)).not.toContain("hidden-value")
						const context = JSON.parse(output).hookSpecificOutput
							.additionalContext as string
						// Session start loads the profile; prompts recall only relevant matches.
						const kind = event === "SessionStart" ? "context" : "match"
						expect(context).toContain(`${kind}:personal`)
						expect(context).toContain(`${kind}:repo_${repo}__`)
						expect(context).not.toContain("noise:")
						if (event === "UserPromptSubmit")
							expect(context).not.toContain("context:")
					}
				}
			}
		} finally {
			await new Promise<void>((resolve) => server.close(() => resolve()))
		}
	})
	test("installs self-hosted hooks and MCP once without writing the key into settings", async () => {
		const home = mkdtempSync(join(tmpdir(), "ram0-agent-install-"))
		cleanup.push(home)
		const calls: string[][] = []
		const runner = async (command: string, args: string[]) => {
			calls.push([command, ...args])
			return { status: 0, output: "" }
		}

		const first = await installAgentIntegrations({
			home,
			baseUrl: "https://brain.example.test/",
			runner,
			apiKey: "sm_synthetic",
		})
		const firstCalls = calls.map((call) => [...call])
		const environment = readFileSync(first.environmentFile, "utf8")
		const marker = readFileSync(first.markerFile, "utf8")
		const second = await installAgentIntegrations({
			home,
			baseUrl: "https://brain.example.test/",
			runner,
			apiKey: "sm_synthetic",
		})

		expect(second.changed).toBe(false)
		expect(calls).toEqual(firstCalls)

		// A pulled hook fix is delivered by a plain rerun.
		const installedRecall = join(
			home,
			".config",
			"ram0-supermemory",
			"recall.mjs",
		)
		writeFileSync(installedRecall, "// stale copy\n")
		const refreshed = await installAgentIntegrations({
			home,
			baseUrl: "https://brain.example.test/",
			runner,
			apiKey: "sm_synthetic",
		})
		expect(refreshed.changed).toBe(true)
		expect(calls).toEqual(firstCalls)
		expect(readFileSync(installedRecall, "utf8")).toBe(
			readFileSync(new URL("./recall.mjs", import.meta.url), "utf8"),
		)
		expect(environment).toContain(
			"SUPERMEMORY_MCP_URL='https://brain.example.test/mcp'",
		)
		expect(environment).toContain(
			'SUPERMEMORY_CODEX_API_KEY="$SUPERMEMORY_API_KEY"',
		)
		expect(environment).toContain(
			'SUPERMEMORY_CC_API_KEY="$SUPERMEMORY_API_KEY"',
		)
		expect(environment).not.toContain("replace-me")
		expect(marker).not.toContain("SUPERMEMORY_API_KEY")
		expect(statSync(first.environmentFile).mode & 0o777).toBe(0o600)
		expect(firstCalls).toContainEqual([
			"codex",
			"mcp",
			"add",
			"supermemory",
			"--env",
			"SUPERMEMORY_MCP_URL=https://brain.example.test/mcp",
			"--",
			"node",
			join(home, ".codex", "supermemory", "mcp-proxy.js"),
		])

		const claudeSettings = readFileSync(
			join(home, ".claude", "settings.json"),
			"utf8",
		)
		expect(JSON.parse(claudeSettings).env).toEqual({
			SUPERMEMORY_API_URL: "https://brain.example.test",
			SUPERMEMORY_MCP_URL: "https://brain.example.test/mcp",
		})
		expect(claudeSettings).not.toContain("sm_synthetic")
		const claudeCredentials = join(
			home,
			".supermemory-claude",
			"credentials.json",
		)
		const codexCredentials = join(
			home,
			".codex",
			"supermemory",
			"credentials.json",
		)
		expect(JSON.parse(readFileSync(claudeCredentials, "utf8")).apiKey).toBe(
			"sm_synthetic",
		)
		expect(JSON.parse(readFileSync(codexCredentials, "utf8"))).toMatchObject({
			apiKey: "sm_synthetic",
			apiBaseUrl: "https://brain.example.test",
		})
		for (const path of [claudeCredentials, codexCredentials])
			expect(statSync(path).mode & 0o777).toBe(0o600)

		const rotated = await installAgentIntegrations({
			home,
			baseUrl: "https://brain.example.test/",
			runner,
			apiKey: "sm_rotated",
		})
		expect(rotated.changed).toBe(true)
		expect(calls).toEqual(firstCalls)
		expect(JSON.parse(readFileSync(codexCredentials, "utf8")).apiKey).toBe(
			"sm_rotated",
		)
	})

	test("leaves client credentials alone without a key", async () => {
		const home = mkdtempSync(join(tmpdir(), "ram0-agent-nokey-"))
		cleanup.push(home)
		await installAgentIntegrations({
			home,
			baseUrl: "https://brain.example.test",
			runner: async () => ({ status: 0, output: "" }),
			apiKey: "",
		})
		expect(existsSync(join(home, ".supermemory-claude"))).toBe(false)
		expect(existsSync(join(home, ".codex", "supermemory"))).toBe(false)
	})

	test("only --force updates the reviewed Claude plugin", async () => {
		const home = mkdtempSync(join(tmpdir(), "ram0-agent-update-"))
		cleanup.push(home)
		const calls: string[][] = []
		const runner = async (command: string, args: string[]) => {
			calls.push([command, ...args])
			return { status: 0, output: "" }
		}
		const update = [
			"claude",
			"plugin",
			"update",
			"supermemory@supermemory-plugins",
			"--scope",
			"user",
		]

		await installAgentIntegrations({
			home,
			baseUrl: "https://brain.example.test",
			runner,
			apiKey: "",
		})
		expect(calls).not.toContainEqual(update)

		await installAgentIntegrations({
			home,
			baseUrl: "https://brain.example.test",
			runner,
			apiKey: "",
			force: true,
		})
		expect(calls).toContainEqual(update)
	})

	test("rejects URLs that could persist credentials", async () => {
		await expect(
			installAgentIntegrations({
				home: "/tmp/unused",
				baseUrl: "https://user:secret@example.test",
				apiKey: "",
				runner: async () => ({ status: 0, output: "" }),
			}),
		).rejects.toThrow("credentials")
	})
})
