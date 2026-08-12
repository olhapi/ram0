"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/use-auth";
import { getErrorMessage } from "@/lib/error-message";
import { getAccountCreationSessionRecovery } from "@/lib/refresh-token-cookie";

const MISSING_INVITATION_MESSAGE =
  "This invitation link is unavailable. Ask the administrator for a new one.";

export default function InvitePage() {
  const router = useRouter();
  const { acceptInvitation } = useAuth();
  const [inviteToken, setInviteToken] = useState<string | null>();
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [showLoginRecovery, setShowLoginRecovery] = useState(false);
  const [recoveryReloadAvailable, setRecoveryReloadAvailable] = useState(false);
  const capturedInviteToken = useRef(false);

  useEffect(() => {
    if (capturedInviteToken.current) return;
    capturedInviteToken.current = true;
    const token = new URLSearchParams(window.location.hash.slice(1)).get(
      "token",
    );
    window.history.replaceState(null, "", "/invite");
    setInviteToken(token);
  }, []);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    setShowLoginRecovery(false);
    setRecoveryReloadAvailable(false);

    if (!inviteToken) {
      setError(MISSING_INVITATION_MESSAGE);
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirmation) {
      setError("Passwords do not match.");
      return;
    }

    setSubmitting(true);
    try {
      await acceptInvitation(inviteToken, password);
      router.push("/dashboard/memories");
    } catch (err) {
      const recovery = getAccountCreationSessionRecovery(err, "activated");
      if (recovery) {
        setError(recovery.message);
        setShowLoginRecovery(true);
        setRecoveryReloadAvailable(recovery.reloadAvailable);
      } else {
        setError(getErrorMessage(err, "Could not accept invitation."));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const unavailable = inviteToken === null;

  return (
    <main className="flex min-h-screen items-center justify-center bg-surface-default-primary p-6">
      <div className="w-full max-w-md rounded-xl border border-memBorder-primary p-8">
        <h1 className="text-center font-fustat text-2xl font-semibold text-onSurface-default-primary">
          Set your password
        </h1>
        <p className="mt-2 text-center text-sm text-onSurface-default-tertiary">
          Complete your invitation to access Ram0.
        </p>

        {inviteToken === undefined ? null : unavailable ? (
          <p
            className="mt-6 rounded bg-surface-danger-primary px-3 py-2 text-sm text-onSurface-danger-primary"
            role="alert"
          >
            {MISSING_INVITATION_MESSAGE}
          </p>
        ) : showLoginRecovery ? (
          <div className="mt-6 space-y-4">
            <p
              className="rounded bg-surface-danger-primary px-3 py-2 text-sm text-onSurface-danger-primary"
              role="alert"
            >
              {error}
            </p>
            {recoveryReloadAvailable && (
              <Button asChild className="w-full" size="lg">
                <Link href="/dashboard/memories">Reload dashboard</Link>
              </Button>
            )}
            <Button asChild className="w-full" size="lg">
              <Link href="/login">Go to sign in</Link>
            </Button>
          </div>
        ) : (
          <form className="mt-6 space-y-4" onSubmit={handleSubmit}>
            {error && (
              <p
                className="rounded bg-surface-danger-primary px-3 py-2 text-sm text-onSurface-danger-primary"
                role="alert"
              >
                {error}
              </p>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="invite-password">Password</Label>
              <Input
                autoFocus
                id="invite-password"
                minLength={8}
                onChange={(event) => setPassword(event.target.value)}
                required
                type="password"
                value={password}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="invite-password-confirmation">
                Confirm password
              </Label>
              <Input
                id="invite-password-confirmation"
                minLength={8}
                onChange={(event) => setConfirmation(event.target.value)}
                required
                type="password"
                value={confirmation}
              />
            </div>
            <Button
              className="w-full"
              disabled={
                submitting ||
                password.length < 8 ||
                confirmation.length < 8 ||
                password !== confirmation
              }
              size="lg"
              type="submit"
            >
              {submitting ? "Activating invitation..." : "Activate account"}
            </Button>
          </form>
        )}
      </div>
    </main>
  );
}
