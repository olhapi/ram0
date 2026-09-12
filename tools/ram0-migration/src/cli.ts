// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { chmod, readFile, writeFile } from "node:fs/promises"
import { pathToFileURL } from "node:url"

import { FileImportJournal } from "./journal"
import { importPlan, SupermemoryImportBackend } from "./import"
import type { ContainerMapping, ImportPlan } from "./model"
import { buildImportPlan } from "./plan"

function option(args: string[], name: string): string {
	const index = args.indexOf(name)
	const value = index >= 0 ? args[index + 1] : undefined
	if (!value || value.startsWith("--")) throw new Error(`${name} is required`)
	return value
}

async function readJson(path: string): Promise<unknown> {
	return JSON.parse(await readFile(path, "utf8"))
}

function mapping(value: unknown): ContainerMapping {
	if (!value || typeof value !== "object") throw new Error("mapping must be a JSON object")
	const personalContainer = Reflect.get(value, "personalContainer")
	const appIds = Reflect.get(value, "appIds")
	if (typeof personalContainer !== "string" || !appIds || typeof appIds !== "object" || Array.isArray(appIds)) {
		throw new Error("mapping requires personalContainer and appIds")
	}
	return { personalContainer, appIds: appIds as Record<string, string> }
}

function plan(value: unknown): ImportPlan {
	if (!value || typeof value !== "object" || Reflect.get(value, "version") !== 1 || !Array.isArray(Reflect.get(value, "records"))) {
		throw new Error("invalid version 1 import plan")
	}
	return value as ImportPlan
}

async function writePrivateJson(path: string, value: unknown): Promise<void> {
	await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", mode: 0o600 })
	await chmod(path, 0o600)
}

export async function run(args: string[], env: Record<string, string | undefined> = process.env): Promise<unknown> {
	const [command] = args
	if (command === "plan") {
		const result = buildImportPlan(
			await readJson(option(args, "--exports")),
			mapping(await readJson(option(args, "--mapping"))),
		)
		await writePrivateJson(option(args, "--output"), result)
		return {
			sourceCount: result.sourceCount,
			records: result.records.length,
			exactDuplicates: result.exactDuplicates,
			invalid: result.invalid.length,
		}
	}
	if (command === "import") {
		const currentPlan = plan(await readJson(option(args, "--plan")))
		if (currentPlan.invalid.length > 0) throw new Error("import plan contains invalid records")
		const apiKey = env.SUPERMEMORY_API_KEY
		if (!apiKey) throw new Error("SUPERMEMORY_API_KEY is required")
		const journal = await FileImportJournal.open(option(args, "--journal"))
		return importPlan(
			currentPlan,
			journal,
			new SupermemoryImportBackend(option(args, "--base-url"), apiKey),
		)
	}
	if (command === "stats") {
		const currentPlan = plan(await readJson(option(args, "--plan")))
		return {
			sourceCount: currentPlan.sourceCount,
			records: currentPlan.records.length,
			exactDuplicates: currentPlan.exactDuplicates,
			invalid: currentPlan.invalid.length,
			containers: [...new Set(currentPlan.records.map((record) => record.containerTag))].sort(),
		}
	}
	throw new Error("usage: migrate <plan|import|stats> [options]")
}

async function main(): Promise<void> {
	const result = await run(process.argv.slice(2))
	process.stdout.write(`${JSON.stringify(result)}\n`)
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
	main().catch((error) => {
		process.stderr.write(`${error instanceof Error ? error.message : "migration failed"}\n`)
		process.exitCode = 1
	})
}
