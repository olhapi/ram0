import assert from "node:assert/strict";
import test from "node:test";

// TypeScript's bundler resolution allows extensionless imports in production;
// Node's native type-stripping runner requires the source extension here.
import { singleFlight } from "../src/lib/single-flight.ts";

test("concurrent calls share one in-flight operation", async () => {
  let calls = 0;
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const run = singleFlight(async () => {
    calls += 1;
    await held;
    return "ready";
  });

  const first = run();
  const second = run();
  release();

  assert.equal(await first, "ready");
  assert.equal(await second, "ready");
  assert.equal(calls, 1);
});

test("a completed operation permits a later call", async () => {
  let calls = 0;
  const run = singleFlight(async () => ++calls);

  assert.equal(await run(), 1);
  assert.equal(await run(), 2);
});
