import assert from "node:assert/strict";
import test from "node:test";

import { SessionInitializationError } from "../src/lib/authenticated-session.ts";

// TypeScript's bundler resolution allows extensionless imports in production;
// Node's native type-stripping runner requires the source extension here.
import {
  getAccountCreationSessionRecovery,
  SessionPersistenceError,
  storeRefreshToken,
} from "../src/lib/refresh-token-cookie.ts";

test("refresh-token storage rejects an unsuccessful cookie response", async () => {
  const failedFetch: typeof fetch = async () =>
    new Response(null, { status: 500 });

  await assert.rejects(
    () => storeRefreshToken("example-refresh-token", failedFetch),
    (error) => error instanceof SessionPersistenceError,
  );
});

test("refresh-token storage translates a network failure", async () => {
  const failedFetch: typeof fetch = async () => {
    throw new Error("network unavailable");
  };

  await assert.rejects(
    () => storeRefreshToken("example-refresh-token", failedFetch),
    (error) => error instanceof SessionPersistenceError,
  );
});

test("refresh-token storage accepts a successful cookie response", async () => {
  let requestBody = "";
  const successfulFetch: typeof fetch = async (_input, init) => {
    requestBody = String(init?.body);
    return new Response(null, { status: 204 });
  };

  await storeRefreshToken("example-refresh-token", successfulFetch);

  assert.deepEqual(JSON.parse(requestBody), {
    refresh_token: "example-refresh-token",
  });
});

test("account creation cookie failure offers a normal login recovery", () => {
  const recovery = getAccountCreationSessionRecovery(
    new SessionPersistenceError(),
    "created",
  );

  assert.equal(recovery?.accountReady, true);
  assert.equal(recovery?.loginHref, "/login");
  assert.equal(recovery?.reloadAvailable, false);
});

test("other registration failures do not claim that an account was created", () => {
  assert.equal(
    getAccountCreationSessionRecovery(
      new Error("registration rejected"),
      "created",
    ),
    null,
  );
});

test("activated account with a saved session cannot retry its invitation", () => {
  const recovery = getAccountCreationSessionRecovery(
    new SessionInitializationError(),
    "activated",
  );

  assert.equal(recovery?.accountReady, true);
  assert.equal(recovery?.reloadAvailable, true);
  assert.equal(recovery?.loginHref, "/login");
});

test("created account with a saved session can recover by reloading", () => {
  const recovery = getAccountCreationSessionRecovery(
    new SessionInitializationError(),
    "created",
  );

  assert.equal(recovery?.accountReady, true);
  assert.equal(recovery?.reloadAvailable, true);
});
