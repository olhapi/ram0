// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { createHash } from "node:crypto"

import type { ContainerMapping, ImportMetadata, ImportPlan, ImportRecord } from "./model"

interface ParsedExport {
	accountId: string
	memories: unknown[]
}

function isObject(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

function parseExports(value: unknown): ParsedExport[] {
	if (!Array.isArray(value)) return []
	return value.map((item) => {
		const current = isObject(item) ? item : {}
		const account = isObject(current.account) ? current.account : {}
		const accountId = typeof account.id === "string" ? account.id : typeof current.accountId === "string" ? current.accountId : "<unknown>"
		const memoryWrapper = isObject(current.memories) ? current.memories : current
		const memories = Array.isArray(memoryWrapper.results) ? memoryWrapper.results : []
		return { accountId, memories }
	})
}

function stringValue(value: unknown): string | undefined {
	return typeof value === "string" && value.length > 0 ? value : undefined
}

function uniqueSorted(values: string[]): string[] {
	return [...new Set(values)].sort()
}

function safeMetadata(raw: unknown): ImportMetadata {
	if (!isObject(raw)) return {}
	const result: ImportMetadata = {}
	for (const [key, value] of Object.entries(raw).sort(([left], [right]) => left.localeCompare(right))) {
		const safeKey = `ram0_metadata_${key.replace(/[^a-zA-Z0-9_:-]/g, "_")}`
		if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") result[safeKey] = value
		else if (Array.isArray(value) && value.every((item) => typeof item === "string")) result[safeKey] = uniqueSorted(value)
	}
	return result
}

function mergeStringArray(metadata: ImportMetadata, key: string, values: string[]): void {
	const current = Array.isArray(metadata[key]) ? (metadata[key] as string[]) : []
	metadata[key] = uniqueSorted([...current, ...values])
}

function createRecord(raw: Record<string, unknown>, accountId: string, containerTag: string, content: string): ImportRecord {
	const memoryId = stringValue(raw.id) ?? "<unknown>"
	const metadata: ImportMetadata = {
		...safeMetadata(raw.metadata),
		ram0_source_ids: [memoryId],
		ram0_account_ids: [accountId],
		ram0_source_count: 1,
	}
	const createdAt = stringValue(raw.created_at)
	const updatedAt = stringValue(raw.updated_at)
	const sourceUser = stringValue(raw.user_id)
	const attributedTo = stringValue(raw.attributed_to)
	const hash = stringValue(raw.hash)
	const appId = stringValue(raw.app_id)
	const categories = Array.isArray(raw.categories) ? raw.categories.filter((item): item is string => typeof item === "string") : []
	if (createdAt) metadata.ram0_created_at = [createdAt]
	if (updatedAt) metadata.ram0_updated_at = [updatedAt]
	if (sourceUser) metadata.ram0_user_ids = [sourceUser]
	if (attributedTo) metadata.ram0_attributed_to = [attributedTo]
	if (hash) metadata.ram0_hashes = [hash]
	if (appId) metadata.ram0_app_ids = [appId]
	if (categories.length > 0) metadata.ram0_categories = uniqueSorted(categories)
	const key = `ram0_${createHash("sha256").update(`${containerTag}\0${content}`).digest("hex")}`
	const expiration = stringValue(raw.expiration_date)
	return { key, content, containerTag, metadata, ...(expiration ? { forgetAfter: expiration } : {}) }
}

function mergeRecord(target: ImportRecord, source: ImportRecord): void {
	for (const key of [
		"ram0_source_ids",
		"ram0_account_ids",
		"ram0_created_at",
		"ram0_updated_at",
		"ram0_user_ids",
		"ram0_attributed_to",
		"ram0_hashes",
		"ram0_app_ids",
		"ram0_categories",
	]) {
		const values = source.metadata[key]
		if (Array.isArray(values)) mergeStringArray(target.metadata, key, values)
	}
	target.metadata.ram0_source_count = (target.metadata.ram0_source_ids as string[]).length
}

export function buildImportPlan(exports: unknown, mapping: ContainerMapping): ImportPlan {
	const recordsByKey = new Map<string, ImportRecord>()
	const invalid: ImportPlan["invalid"] = []
	let sourceCount = 0
	let exactDuplicates = 0

	for (const accountExport of parseExports(exports)) {
		for (const [index, value] of accountExport.memories.entries()) {
			sourceCount += 1
			const raw = isObject(value) ? value : {}
			const memoryId = stringValue(raw.id) ?? `#${index}`
			const content = typeof raw.memory === "string" ? raw.memory.trim() : ""
			if (!content) {
				invalid.push({ accountId: accountExport.accountId, memoryId, reason: "memory text is empty" })
				continue
			}
			if (content.length > 10_000) {
				invalid.push({ accountId: accountExport.accountId, memoryId, reason: "memory text exceeds 10000 characters" })
				continue
			}
			const appId = stringValue(raw.app_id)
			const containerTag = appId ? mapping.appIds[appId] : mapping.personalContainer
			if (!containerTag) {
				invalid.push({ accountId: accountExport.accountId, memoryId, reason: `unmapped app_id: ${appId}` })
				continue
			}
			if (!/^[a-zA-Z0-9_:-]{1,100}$/.test(containerTag)) {
				invalid.push({ accountId: accountExport.accountId, memoryId, reason: `invalid container tag: ${containerTag}` })
				continue
			}

			const record = createRecord(raw, accountExport.accountId, containerTag, content)
			const existing = recordsByKey.get(record.key)
			if (existing) {
				exactDuplicates += 1
				mergeRecord(existing, record)
			} else {
				recordsByKey.set(record.key, record)
			}
		}
	}

	const records = [...recordsByKey.values()].sort((left, right) => left.containerTag.localeCompare(right.containerTag) || left.content.localeCompare(right.content) || left.key.localeCompare(right.key))
	return { version: 1, sourceCount, exactDuplicates, invalid, records }
}
