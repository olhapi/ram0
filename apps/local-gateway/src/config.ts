// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

export interface GatewayConfig {
	host: string
	port: number
	engineUrl: string
	apiKey: string
	personalContainer: string
}

const DEFAULT_ENGINE_URL = "http://supermemory-engine:6767"

export function loadConfig(env: Record<string, string | undefined>): GatewayConfig {
	const apiKey = env.SUPERMEMORY_API_KEY
	if (!apiKey) {
		throw new Error("SUPERMEMORY_API_KEY is required")
	}

	const rawPort = env.GATEWAY_PORT ?? "8000"
	const port = Number(rawPort)
	if (!Number.isInteger(port) || port < 1 || port > 65_535 || String(port) !== rawPort) {
		throw new Error("GATEWAY_PORT must be an integer between 1 and 65535")
	}

	const rawEngineUrl = env.SUPERMEMORY_ENGINE_URL ?? DEFAULT_ENGINE_URL
	let engineUrl: URL
	try {
		engineUrl = new URL(rawEngineUrl)
	} catch {
		throw new Error("SUPERMEMORY_ENGINE_URL must be a valid URL")
	}
	if (!["http:", "https:"].includes(engineUrl.protocol)) {
		throw new Error("SUPERMEMORY_ENGINE_URL must use http or https")
	}

	const personalContainer = env.SUPERMEMORY_PERSONAL_CONTAINER ?? "personal"
	if (!personalContainer) {
		throw new Error("SUPERMEMORY_PERSONAL_CONTAINER cannot be empty")
	}

	return {
		host: env.GATEWAY_HOST ?? "0.0.0.0",
		port,
		engineUrl: engineUrl.toString().replace(/\/$/, ""),
		apiKey,
		personalContainer,
	}
}
