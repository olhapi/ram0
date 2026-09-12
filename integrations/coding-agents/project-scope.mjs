#!/usr/bin/env node
// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { execFileSync } from "node:child_process"
import { createHash } from "node:crypto"
import { realpathSync } from "node:fs"
import { basename, resolve } from "node:path"
import { pathToFileURL } from "node:url"

function git(args, cwd) {
	try {
		return execFileSync("git", args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim() || null
	} catch {
		return null
	}
}

function shortHash(value) {
	return createHash("sha256").update(value).digest("hex").slice(0, 16)
}

export function normalizeGitRemote(remoteUrl) {
	const raw = remoteUrl.trim()
	if (!raw) return null

	let normalized
	if (/^[a-z][a-z\d+.-]*:\/\//i.test(raw)) {
		try {
			const parsed = new URL(raw)
			normalized =
				parsed.protocol === "file:"
					? `file:${decodeURIComponent(parsed.pathname)}`
					: `${parsed.hostname.toLowerCase()}${parsed.port ? `:${parsed.port}` : ""}/${parsed.pathname.replace(/^\/+/, "")}`
		} catch {
			normalized = raw
		}
	} else {
		const scpStyle = raw.match(/^(?:[^@/]+@)?([^:]+):(.+)$/)
		normalized = scpStyle ? `${scpStyle[1].toLowerCase()}/${scpStyle[2]}` : `file:${resolve(raw)}`
	}

	return normalized
		.replace(/[?#].*$/, "")
		.replace(/\/+$/, "")
		.replace(/\.git$/i, "")
		.replace(/\/{2,}/g, "/")
		.toLowerCase()
}

function sanitizeRepositoryName(name) {
	const sanitized = name
		.toLowerCase()
		.replace(/[^a-z0-9]/g, "_")
		.replace(/_+/g, "_")
		.replace(/^_|_$/g, "")
	return (sanitized || "unknown").slice(0, 72).replace(/_+$/g, "") || "unknown"
}

export function resolveRepositoryContainer(cwd = process.cwd()) {
	const root = git(["rev-parse", "--show-toplevel"], cwd) ?? resolve(cwd)
	const remote = git(["remote", "get-url", "origin"], root)
	let repositoryName = basename(root) || "unknown"
	let identity

	if (remote) {
		const display = remote.replace(/\/+$/, "").replace(/\.git$/i, "")
		const separator = Math.max(display.lastIndexOf("/"), display.lastIndexOf(":"))
		repositoryName = display.slice(separator + 1) || repositoryName
		identity = normalizeGitRemote(remote)
	}

	if (!identity) {
		let canonicalPath = root
		try {
			canonicalPath = realpathSync.native(root)
		} catch {}
		identity = `path:${canonicalPath}`
	}

	return `repo_${sanitizeRepositoryName(repositoryName)}__${shortHash(identity)}`
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
	process.stdout.write(`${resolveRepositoryContainer(process.argv[2] ?? process.cwd())}\n`)
}
