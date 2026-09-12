// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: MIT

export function resolveContainers(project?: string, personal = "personal"): string[] {
	return project && project !== personal ? [personal, project] : [personal]
}
