// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import {
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
			})
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
					for (const event of ["SessionStart", "UserPromptSubmit"]) {
						const command = settings.hooks[event]
							?.flatMap((group) => group.hooks)
							.find((hook) => hook.command.includes("recall.mjs"))?.command
						if (!command) throw new Error("Recall hook not installed")
						const before = requests.length
						const output = await new Promise<string>((resolve, reject) => {
							const child = spawn("bash", ["-c", command], {
								cwd,
								env: {
									...process.env,
									SUPERMEMORY_API_URL: baseUrl,
									SUPERMEMORY_API_KEY: "synthetic-test-key",
								},
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
						expect(
							JSON.parse(output).hookSpecificOutput.additionalContext,
						).toContain("context:personal")
						expect(
							JSON.parse(output).hookSpecificOutput.additionalContext,
						).toContain(`context:repo_${repo}__`)
					}
				}
			}
		} finally {
			await new Promise<void>((resolve) => server.close(() => resolve()))
		}
	})
	test("installs self-hosted hooks and MCP once without persisting the key", async () => {
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
		})
		const firstCalls = calls.map((call) => [...call])
		const environment = readFileSync(first.environmentFile, "utf8")
		const marker = readFileSync(first.markerFile, "utf8")
		const second = await installAgentIntegrations({
			home,
			baseUrl: "https://brain.example.test/",
			runner,
		})

		expect(second.changed).toBe(false)
		expect(calls).toEqual(firstCalls)
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
		expect(
			firstCalls.some((call) =>
				call.join(" ").includes("--bearer-token-env-var SUPERMEMORY_API_KEY"),
			),
		).toBe(true)
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
