#!/usr/bin/env node
// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { pathToFileURL } from "node:url"

export async function requeueFailedDocuments({
	baseUrl,
	apiKey,
	apply = false,
	fetchImpl = fetch,
	log = console.log,
}) {
	const api = async (path, init = {}) => {
		const response = await fetchImpl(`${baseUrl.replace(/\/+$/, "")}${path}`, {
			...init,
			headers: {
				Authorization: `Bearer ${apiKey}`,
				"content-type": "application/json",
			},
			signal: AbortSignal.timeout(30_000),
		})
		if (!response.ok)
			throw new Error(
				`${init.method ?? "GET"} ${path} returned ${response.status}`,
			)
		return response.json()
	}

	const failed = []
	for (let page = 1; ; page += 1) {
		const { memories, pagination } = await api("/v3/documents/list", {
			method: "POST",
			body: JSON.stringify({ page, limit: 100, includeContent: false }),
		})
		failed.push(...memories.filter((document) => document.status === "failed"))
		if (page >= (pagination?.totalPages ?? 1)) break
	}

	const summary = { failed: failed.length, requeued: 0, skipped: 0 }
	for (const document of failed) {
		const [containerTag, ...extraTags] = document.containerTags ?? []
		// Re-posting under the same customId replaces the document and schedules it again; without one it would duplicate.
		if (!document.customId || !containerTag || extraTags.length > 0) {
			summary.skipped += 1
			log(`skip ${document.id}: needs a customId and exactly one container`)
			continue
		}
		if (!apply) {
			log(`would requeue ${document.id} in ${containerTag}`)
			continue
		}
		const { content, metadata } = await api(
			`/v3/documents/${encodeURIComponent(document.id)}`,
		)
		if (typeof content !== "string" || !content.trim()) {
			summary.skipped += 1
			log(`skip ${document.id}: no stored content`)
			continue
		}
		await api("/v3/documents", {
			method: "POST",
			body: JSON.stringify({
				content,
				containerTag,
				customId: document.customId,
				...(metadata ? { metadata } : {}),
			}),
		})
		summary.requeued += 1
		log(`requeued ${document.id} in ${containerTag}`)
	}
	return summary
}

if (
	process.argv[1] &&
	import.meta.url === pathToFileURL(process.argv[1]).href
) {
	const args = process.argv.slice(2)
	const baseUrl = args[args.indexOf("--base-url") + 1]
	const apply = args.includes("--apply")
	if (
		!args.includes("--base-url") ||
		!baseUrl ||
		!process.env.SUPERMEMORY_API_KEY
	) {
		process.stderr.write(
			"usage: SUPERMEMORY_API_KEY=... requeue-failed-documents.mjs --base-url <api-url> [--apply]\n",
		)
		process.exit(2)
	}
	requeueFailedDocuments({
		baseUrl,
		apiKey: process.env.SUPERMEMORY_API_KEY,
		apply,
	})
		.then((summary) => {
			process.stdout.write(
				`${apply ? "" : "dry run: "}${summary.failed} failed, ${summary.requeued} requeued, ${summary.skipped} skipped\n`,
			)
		})
		.catch((error) => {
			process.stderr.write(
				`${error instanceof Error ? error.message : "requeue failed"}\n`,
			)
			process.exitCode = 1
		})
}
