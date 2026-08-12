import { SessionInitializationError } from "./authenticated-session.ts";

export class SessionPersistenceError extends Error {
  constructor() {
    super("The browser could not save your session.");
    this.name = "SessionPersistenceError";
  }
}

export interface AccountCreationSessionRecovery {
  accountReady: true;
  loginHref: "/login";
  message: string;
  reloadAvailable: boolean;
}

export function getAccountCreationSessionRecovery(
  error: unknown,
  outcome: "activated" | "created",
): AccountCreationSessionRecovery | null {
  const accountState = outcome === "activated" ? "activated" : "created";

  if (error instanceof SessionInitializationError) {
    return {
      accountReady: true,
      loginHref: "/login",
      message: `Your account was ${accountState} and your session was saved, but the dashboard could not finish loading. Reload the dashboard or sign in again.`,
      reloadAvailable: true,
    };
  }

  if (!(error instanceof SessionPersistenceError)) return null;

  return {
    accountReady: true,
    loginHref: "/login",
    message: `Your account was ${accountState}, but automatic sign-in could not be completed. Sign in with your email and password.`,
    reloadAvailable: false,
  };
}

export async function storeRefreshToken(
  refreshToken: string,
  fetchRequest: typeof fetch = fetch,
) {
  let response: Response;
  try {
    response = await fetchRequest("/api/auth/refresh", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
  } catch {
    throw new SessionPersistenceError();
  }

  if (!response.ok) throw new SessionPersistenceError();
}
