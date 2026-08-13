// SPDX-FileCopyrightText: 2026 Ram0 contributors
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const apiTypes = readFileSync(
  new URL("../src/types/api.ts", import.meta.url),
  "utf8",
);
const memoriesPage = readFileSync(
  new URL("../src/app/(root)/dashboard/memories/page.tsx", import.meta.url),
  "utf8",
);
const entitiesPage = readFileSync(
  new URL("../src/app/(root)/dashboard/entities/page.tsx", import.meta.url),
  "utf8",
);

test("dashboard exposes project and global memory scope", () => {
  assert.ok(apiTypes.includes("app_id?: string"));
  assert.ok(apiTypes.includes('"app" | "agent" | "run"'));
  assert.ok(memoriesPage.includes('label: "Project"'));
  assert.ok(memoriesPage.includes("Global"));
  assert.ok(entitiesPage.includes('value="app"'));
  assert.ok(entitiesPage.includes('app: "Project"'));
  assert.ok(entitiesPage.includes("{ENTITY_TYPE_LABELS[value]}"));
});

test("project deletion reuses the entity confirmation flow", () => {
  assert.ok(entitiesPage.includes("ENTITY_ENDPOINTS.BY_ID"));
  assert.ok(entitiesPage.includes("<DeleteConfirmationModal"));
  assert.ok(!entitiesPage.includes("PROJECT_ENDPOINTS"));
});
