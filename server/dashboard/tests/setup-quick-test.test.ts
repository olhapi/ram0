import assert from "node:assert/strict";
import test from "node:test";

import { buildSetupQuickTestPayload } from "../src/lib/setup-quick-test.ts";

test("setup Quick Test derives memory ownership from its API key", () => {
  assert.deepEqual(buildSetupQuickTestPayload("I like private trails."), {
    messages: [{ role: "user", content: "I like private trails." }],
  });
});
