import assert from "node:assert/strict";
import test from "node:test";

// TypeScript's bundler resolution allows extensionless imports in production;
// Node's native type-stripping runner requires the source extension here.
import { RequestGate } from "../src/lib/request-gate.ts";

test("invalidating a request makes its eventual response stale", () => {
  const gate = new RequestGate();
  const request = gate.begin();

  assert.notEqual(request, null);
  assert.equal(gate.isCurrent(request!), true);

  gate.invalidate();

  assert.equal(gate.isCurrent(request!), false);
  assert.equal(gate.isPending, false);
});

test("a pending request prevents duplicate submissions", () => {
  const gate = new RequestGate();
  const first = gate.begin();

  assert.notEqual(first, null);
  assert.equal(gate.begin(), null);

  gate.finish(first!);

  assert.equal(gate.isPending, false);
  assert.notEqual(gate.begin(), null);
});

test("a successful stale request refreshes without revealing its secret", () => {
  const gate = new RequestGate();
  const request = gate.begin();

  assert.notEqual(request, null);
  gate.invalidate();

  assert.deepEqual(gate.successDisposition(request!), {
    revealSecret: false,
    refresh: true,
  });
});

test("a successful request after disposal has no component effects", () => {
  const gate = new RequestGate();
  const request = gate.begin();

  assert.notEqual(request, null);
  gate.dispose();

  assert.deepEqual(gate.successDisposition(request!), {
    revealSecret: false,
    refresh: false,
  });
});
