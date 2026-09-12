// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

import { createHash, timingSafeEqual } from "node:crypto"

function digest(value: string): Buffer {
	return createHash("sha256").update(value).digest()
}

export function authenticateBearer(header: string | undefined, expected: string): boolean {
	if (!header || !expected) {
		return false
	}

	const match = /^Bearer ([^ ]+)$/i.exec(header)
	if (!match) {
		return false
	}

	return timingSafeEqual(digest(match[1]), digest(expected))
}
