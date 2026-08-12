import assert from "node:assert/strict";
import test from "node:test";

import {
  getSessionInitializationRecovery,
  persistAuthenticatedSession,
  SessionInitializationError,
} from "../src/lib/authenticated-session.ts";
import { storeRefreshToken } from "../src/lib/refresh-token-cookie.ts";
import { getAccessToken, setAccessToken } from "../src/utils/api.ts";

const tokens = {
  access_token: "example-access-token",
  refresh_token: "example-refresh-token",
};

const failedCookieFetch: typeof fetch = async () =>
  new Response(null, { status: 500 });

async function assertCookieFailureLeavesNoAuthenticatedState() {
  let loadCount = 0;
  setAccessToken("stale-access-token");

  await assert.rejects(() =>
    persistAuthenticatedSession(tokens, {
      loadUser: async () => {
        loadCount += 1;
      },
      persistRefreshToken: (refreshToken) =>
        storeRefreshToken(refreshToken, failedCookieFetch),
      setAccessToken,
    }),
  );

  assert.equal(getAccessToken(), null);
  assert.equal(loadCount, 0);
}

test("login cookie PUT failure leaves the API singleton unauthenticated", async () => {
  await assertCookieFailureLeavesNoAuthenticatedState();
});

test("registration cookie PUT failure leaves the API singleton unauthenticated", async () => {
  await assertCookieFailureLeavesNoAuthenticatedState();
});

async function assertLoadUserFailurePreservesOnlyTheRefreshSession() {
  let cookieWriteCount = 0;
  let caught: unknown;
  setAccessToken("stale-access-token");

  try {
    await persistAuthenticatedSession(tokens, {
      loadUser: async () => {
        throw new Error("profile temporarily unavailable");
      },
      persistRefreshToken: (refreshToken) =>
        storeRefreshToken(refreshToken, async () => {
          cookieWriteCount += 1;
          return new Response(null, { status: 204 });
        }),
      setAccessToken,
    });
  } catch (error) {
    caught = error;
  }

  assert.ok(caught instanceof SessionInitializationError);
  assert.equal(cookieWriteCount, 1);
  assert.equal(getAccessToken(), null);
  assert.deepEqual(getSessionInitializationRecovery(caught), {
    reloadAvailable: true,
    sessionSaved: true,
  });
}

test("login loadUser failure becomes recoverable session initialization", async () => {
  await assertLoadUserFailurePreservesOnlyTheRefreshSession();
});

test("registration loadUser failure becomes recoverable session initialization", async () => {
  await assertLoadUserFailurePreservesOnlyTheRefreshSession();
});

test("invitation loadUser failure becomes recoverable session initialization", async () => {
  await assertLoadUserFailurePreservesOnlyTheRefreshSession();
});
