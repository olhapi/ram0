interface AuthTokens {
  access_token: string;
  refresh_token: string;
}

interface AuthenticatedSessionDependencies {
  loadUser: () => Promise<void>;
  persistRefreshToken: (refreshToken: string) => Promise<void>;
  setAccessToken: (accessToken: string | null) => void;
}

export class SessionInitializationError extends Error {
  readonly cause: unknown;

  constructor(cause?: unknown) {
    super(
      "Your session was saved, but the dashboard could not finish loading.",
    );
    this.name = "SessionInitializationError";
    this.cause = cause;
  }
}

export function getSessionInitializationRecovery(error: unknown) {
  if (!(error instanceof SessionInitializationError)) return null;
  return { reloadAvailable: true as const, sessionSaved: true as const };
}

export async function persistAuthenticatedSession(
  tokens: AuthTokens,
  dependencies: AuthenticatedSessionDependencies,
) {
  dependencies.setAccessToken(null);
  await dependencies.persistRefreshToken(tokens.refresh_token);
  dependencies.setAccessToken(tokens.access_token);
  try {
    await dependencies.loadUser();
  } catch (error) {
    dependencies.setAccessToken(null);
    throw new SessionInitializationError(error);
  }
}
