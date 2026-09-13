#!/usr/bin/env node
// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { spawn } from "node:child_process"
import { chmod, mkdir, readFile, rename, writeFile } from "node:fs/promises"
import { homedir } from "node:os"
import { dirname, join } from "node:path"
import { pathToFileURL } from "node:url"

const CODEX_PLUGIN_VERSION = "1.0.17"
const CLAUDE_PLUGIN_SOURCE = "supermemoryai/claude-supermemory"
const MARKER_VERSION = 3

function normalizedBaseUrl(value) {
	let url
	try {
		url = new URL(value)
	} catch {
		throw new Error("base URL must be an absolute http(s) URL")
	}
	if (!["http:", "https:"].includes(url.protocol))
		throw new Error("base URL must use http or https")
	if (url.username || url.password)
		throw new Error("base URL must not contain credentials")
	if (url.search || url.hash)
		throw new Error("base URL must not contain a query or fragment")
	url.pathname = url.pathname.replace(/\/+$/, "")
	return url.toString().replace(/\/$/, "")
}

function shellQuote(value) {
	return `'${value.replaceAll("'", `'"'"'`)}'`
}

async function writePrivate(path, content) {
	await mkdir(dirname(path), { recursive: true, mode: 0o700 })
	const temporary = `${path}.new`
	await writeFile(temporary, content, { encoding: "utf8", mode: 0o600 })
	await chmod(temporary, 0o600)
	await rename(temporary, path)
}

async function defaultRunner(command, args, options = {}) {
	return new Promise((resolve, reject) => {
		const child = spawn(command, args, {
			cwd: options.cwd,
			env: { ...process.env, HOME: options.home, USERPROFILE: options.home },
			stdio: ["ignore", "pipe", "pipe"],
		})
		let output = ""
		child.stdout.on("data", (chunk) => {
			output += chunk.toString()
		})
		child.stderr.on("data", (chunk) => {
			output += chunk.toString()
		})
		child.once("error", reject)
		child.once("close", (status) => resolve({ status: status ?? 1, output }))
	})
}

function commandPlan(home, mcpUrl) {
	return [
		{
			command: "npx",
			args: ["--yes", `codex-supermemory@${CODEX_PLUGIN_VERSION}`, "install"],
		},
		{
			command: "codex",
			args: ["mcp", "remove", "supermemory"],
			allowedFailure: /not found|does not exist/i,
		},
		{
			command: "codex",
			args: [
				"mcp",
				"add",
				"supermemory",
				"--env",
				`SUPERMEMORY_MCP_URL=${mcpUrl}`,
				"--",
				"node",
				join(home, ".codex", "supermemory", "mcp-proxy.js"),
			],
		},
		{
			command: "claude",
			args: [
				"plugin",
				"marketplace",
				"add",
				CLAUDE_PLUGIN_SOURCE,
				"--scope",
				"user",
			],
			allowedFailure: /already (?:exists|configured|added)/i,
		},
		{
			command: "claude",
			args: [
				"plugin",
				"install",
				"supermemory@supermemory-plugins",
				"--scope",
				"user",
				"--yes",
			],
			allowedFailure: /already installed/i,
		},
	]
}

// Upstream login writes these files; seeding them keeps the key out of settings.
async function syncCredentials(home, apiUrl, apiKey) {
	if (!apiKey) return false
	let changed = false
	for (const [path, extra] of [
		[join(home, ".supermemory-claude", "credentials.json"), {}],
		[
			join(home, ".codex", "supermemory", "credentials.json"),
			{ apiBaseUrl: apiUrl },
		],
	]) {
		const current = await readJson(path)
		if (
			current?.apiKey === apiKey &&
			Object.entries(extra).every(([key, value]) => current[key] === value)
		)
			continue
		await writePrivate(
			path,
			`${JSON.stringify({ apiKey, ...extra, savedAt: new Date().toISOString() }, null, 2)}\n`,
		)
		changed = true
	}
	return changed
}

async function readJson(path) {
	try {
		return JSON.parse(await readFile(path, "utf8"))
	} catch {
		return null
	}
}

async function installRecallHooks(home, configDirectory, apiUrl, mcpUrl) {
	for (const filename of ["recall.mjs", "project-scope.mjs"]) {
		await writePrivate(
			join(configDirectory, filename),
			await readFile(new URL(filename, import.meta.url), "utf8"),
		)
	}
	const command = `node ${shellQuote(join(configDirectory, "recall.mjs"))}`
	for (const [directory, filename] of [
		[".codex", "hooks.json"],
		[".claude", "settings.json"],
	]) {
		const path = join(home, directory, filename)
		let settings = {}
		try {
			settings = JSON.parse(await readFile(path, "utf8"))
		} catch (error) {
			if (error.code !== "ENOENT")
				throw new Error(`Cannot safely merge ${directory}/${filename}`)
		}
		const events = settings.hooks ?? (directory === ".codex" ? settings : {})
		for (const event of ["SessionStart", "UserPromptSubmit"]) {
			const groups = events[event] ?? []
			if (
				!groups.some((group) =>
					group.hooks.some((hook) => hook.command === command),
				)
			)
				groups.push({ hooks: [{ type: "command", command, timeout: 10 }] })
			events[event] = groups
		}
		if (events === settings) settings = { hooks: events }
		else settings.hooks = events
		// GUI and service launchers skip shell profiles; Claude Code applies this to plugin hooks and MCP.
		if (directory === ".claude")
			settings.env = {
				...settings.env,
				SUPERMEMORY_API_URL: apiUrl,
				SUPERMEMORY_MCP_URL: mcpUrl,
			}
		await writePrivate(path, `${JSON.stringify(settings, null, 2)}\n`)
	}
}

export async function installAgentIntegrations({
	home = homedir(),
	baseUrl,
	runner = defaultRunner,
	force = false,
	apiKey = process.env.SUPERMEMORY_API_KEY,
} = {}) {
	if (!baseUrl) throw new Error("--base-url is required")
	const apiUrl = normalizedBaseUrl(baseUrl)
	const mcpUrl = `${apiUrl}/mcp`
	const configDirectory = join(home, ".config", "ram0-supermemory")
	const environmentFile = join(configDirectory, "env.sh")
	const markerFile = join(configDirectory, "installed.json")
	const marker = {
		version: MARKER_VERSION,
		apiUrl,
		mcpUrl,
		codexPluginVersion: CODEX_PLUGIN_VERSION,
		claudePluginSource: CLAUDE_PLUGIN_SOURCE,
	}
	const previous = await readJson(markerFile)
	if (!force && JSON.stringify(previous) === JSON.stringify(marker)) {
		const changed = await syncCredentials(home, apiUrl, apiKey)
		return { changed, environmentFile, markerFile, commands: [] }
	}

	const environment =
		"# Generated by Ram0 Supermemory. Set SUPERMEMORY_API_KEY separately.\n" +
		`export SUPERMEMORY_API_URL=${shellQuote(apiUrl)}\n` +
		`export SUPERMEMORY_MCP_URL=${shellQuote(mcpUrl)}\n` +
		`if [ -n "\${SUPERMEMORY_API_KEY:-}" ]; then\n` +
		`  export SUPERMEMORY_CODEX_API_KEY="$SUPERMEMORY_API_KEY"\n` +
		`  export SUPERMEMORY_CC_API_KEY="$SUPERMEMORY_API_KEY"\n` +
		"fi\n"
	await writePrivate(environmentFile, environment)

	const commands = commandPlan(home, mcpUrl)
	for (const step of commands) {
		const result = await runner(step.command, step.args, { home })
		if (result.status !== 0 && !step.allowedFailure?.test(result.output)) {
			throw new Error(
				`${step.command} integration command failed: ${result.output.trim() || `exit ${result.status}`}`,
			)
		}
	}
	await installRecallHooks(home, configDirectory, apiUrl, mcpUrl)
	await writePrivate(markerFile, `${JSON.stringify(marker, null, 2)}\n`)
	// Only after the gateway URLs are configured, so the key never reaches the upstream default.
	await syncCredentials(home, apiUrl, apiKey)
	return {
		changed: true,
		environmentFile,
		markerFile,
		commands: commands.map(({ command, args }) => [command, ...args]),
	}
}

function parseArguments(args) {
	let baseUrl
	let force = false
	for (let index = 0; index < args.length; index += 1) {
		if (args[index] === "--base-url") baseUrl = args[++index]
		else if (args[index] === "--force") force = true
		else throw new Error(`unknown argument: ${args[index]}`)
	}
	return { baseUrl, force }
}

if (
	process.argv[1] &&
	import.meta.url === pathToFileURL(process.argv[1]).href
) {
	installAgentIntegrations(parseArguments(process.argv.slice(2)))
		.then((result) => {
			process.stdout.write(
				result.changed
					? "Installed Claude Code and Codex Supermemory integration. Restart both clients.\n"
					: "Claude Code and Codex Supermemory integration is already current.\n",
			)
			if (!process.env.SUPERMEMORY_API_KEY)
				process.stderr.write(
					"SUPERMEMORY_API_KEY is not set, so client credentials were not stored. Source your credentials file and run the installer again.\n",
				)
		})
		.catch((error) => {
			process.stderr.write(
				`${error instanceof Error ? error.message : "installation failed"}\n`,
			)
			process.exitCode = 1
		})
}
