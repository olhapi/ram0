"use client";

// Modified for Ram0; see NOTICE and repository history.

import { useEffect, useState } from "react";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";
import { Check, Copy } from "lucide-react";
import { CopyToClipboard } from "react-copy-to-clipboard";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/use-auth";
import { getSessionInitializationRecovery } from "@/lib/authenticated-session";
import { getErrorMessage } from "@/lib/error-message";
import { isValidEmail } from "@/lib/validators";

const RESET_COMMAND =
  "make reset-admin-password EMAIL=<your-email> PASSWORD=<new-password>";

export default function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, isLoading, login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [showSessionRecovery, setShowSessionRecovery] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!isLoading && user) {
      router.push(searchParams.get("next") || "/dashboard/requests");
    }
  }, [user, isLoading, router, searchParams]);

  const emailValid = isValidEmail(email);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setShowSessionRecovery(false);
    if (!emailValid) {
      setError("Enter a valid email address.");
      return;
    }
    setSubmitting(true);
    try {
      await login(email, password);
      router.push(searchParams.get("next") || "/dashboard/requests");
    } catch (err) {
      if (getSessionInitializationRecovery(err)) {
        setError(
          "Sign-in succeeded and your session was saved, but the dashboard could not finish loading. Reload to try again.",
        );
        setShowSessionRecovery(true);
      } else {
        setError(getErrorMessage(err, "Login failed"));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen bg-surface-default-primary">
      <div className="flex flex-1 items-center justify-center p-8">
        <div className="w-full max-w-md">
          <div className="flex justify-center mb-2">
            <Image
              src="/images/ram0-mark.svg"
              alt="Ram0"
              width={48}
              height={48}
              priority
            />
          </div>
          <h1 className="text-2xl font-semibold text-onSurface-default-primary text-center mb-6 font-fustat">
            Sign in to Ram0
          </h1>
          <div className="flex flex-col gap-4 border p-8 border-memBorder-primary rounded-xl">
            {error && (
              <p className="text-sm text-onSurface-danger-primary bg-surface-danger-primary px-3 py-2 rounded">
                {error}
              </p>
            )}
            {showSessionRecovery ? (
              <Button
                className="w-full"
                onClick={() => window.location.reload()}
                size="lg"
                type="button"
              >
                Reload session
              </Button>
            ) : (
              <form onSubmit={handleSubmit} className="flex flex-col gap-4">
                <div className="space-y-1.5">
                  <Label htmlFor="login-email">Email</Label>
                  <Input
                    id="login-email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="admin@company.com"
                    required
                    autoFocus
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="login-password">Password</Label>
                  <Input
                    id="login-password"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                  />
                </div>
                <Button
                  type="submit"
                  disabled={submitting || !emailValid || !password}
                  variant="default"
                  size="lg"
                  className="w-full"
                >
                  {submitting ? "Signing in..." : "Sign in"}
                </Button>
              </form>
            )}
            {!showSessionRecovery && (
              <Dialog>
                <DialogTrigger asChild>
                  <button
                    type="button"
                    className="text-xs text-onSurface-default-tertiary hover:text-onSurface-default-primary underline underline-offset-4 self-center"
                  >
                    Forgot password?
                  </button>
                </DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>Reset your admin password</DialogTitle>
                    <DialogDescription>
                      Run this command on the server host. It overwrites the
                      existing password; anyone already signed in stays signed
                      in until their session expires.
                    </DialogDescription>
                  </DialogHeader>
                  <div className="flex gap-2">
                    <Input
                      readOnly
                      value={RESET_COMMAND}
                      className="font-mono text-xs"
                    />
                    <CopyToClipboard
                      text={RESET_COMMAND}
                      onCopy={() => {
                        setCopied(true);
                        setTimeout(() => setCopied(false), 2000);
                      }}
                    >
                      <Button variant="outline" size="icon">
                        {copied ? (
                          <Check className="size-4" />
                        ) : (
                          <Copy className="size-4" />
                        )}
                      </Button>
                    </CopyToClipboard>
                  </div>
                </DialogContent>
              </Dialog>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
