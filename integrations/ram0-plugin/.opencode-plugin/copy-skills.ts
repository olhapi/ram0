// Modified for Ram0; see NOTICE and repository history.
// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: Apache-2.0

import {cp, mkdir, readdir, rm} from "node:fs/promises";
import {dirname, resolve} from "node:path";
import {fileURLToPath} from "node:url";

export const RAM0_SKILL_NAMES = [
  "dream", "export", "forget", "health", "import", "memory-reviewer",
  "onboard", "peek", "ram0-memory", "remember", "stats", "tour",
] as const;

export async function copySkills(packageDirectory = dirname(fileURLToPath(import.meta.url))): Promise<string> {
  const sourceDirectory = resolve(packageDirectory, "../skills");
  const outputDirectory = resolve(packageDirectory, "opencode-skills");
  const sourceNames = (await readdir(sourceDirectory, {withFileTypes: true}))
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort();
  if (sourceNames.join("\0") !== RAM0_SKILL_NAMES.join("\0")) {
    throw new Error(`unexpected Ram0 skill set: ${sourceNames.join(", ")}`);
  }

  await rm(outputDirectory, {recursive: true, force: true});
  await mkdir(outputDirectory, {recursive: true});
  for (const name of RAM0_SKILL_NAMES) {
    await cp(resolve(sourceDirectory, name), resolve(outputDirectory, name), {recursive: true});
  }
  return outputDirectory;
}

if (import.meta.main) await copySkills();
