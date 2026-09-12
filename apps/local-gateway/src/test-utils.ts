// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import type { Server } from "node:http"
import type { AddressInfo } from "node:net"

export async function listen(server: Server): Promise<string> {
	await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
	const { port } = server.address() as AddressInfo
	return `http://127.0.0.1:${port}`
}

export function closeServer(server: Server): Promise<void> {
	return new Promise<void>((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())))
}
