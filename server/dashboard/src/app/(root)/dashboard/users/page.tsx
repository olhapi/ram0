"use client";

import { useEffect, useRef, useState } from "react";
import {
  Check,
  Copy,
  Plus,
  Trash2,
  UserRoundCheck,
  UserRoundX,
} from "lucide-react";
import { CopyToClipboard } from "react-copy-to-clipboard";
import { useRouter } from "next/navigation";
import { format } from "date-fns";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import DeleteConfirmationModal from "@/components/ui/delete-confirmation-modal";
import { DataTable } from "@/components/shared/data-table";
import { TableSkeleton } from "@/components/shared/table-skeleton";
import { EmptyState } from "@/components/self-hosted/empty-state";
import { toast } from "@/components/ui/use-toast";
import { useApiQuery } from "@/hooks/use-api-query";
import { useAuth } from "@/hooks/use-auth";
import { getErrorMessage } from "@/lib/error-message";
import { RequestGate } from "@/lib/request-gate";
import { isValidEmail } from "@/lib/validators";
import {
  AdminUser,
  InvitationCreateResponse,
  PendingInvitation,
  UsersResponse,
} from "@/types/api";
import { api } from "@/utils/api";
import { USER_ENDPOINTS } from "@/utils/api-endpoints";

type UsersTransportResponse = Omit<UsersResponse, "pending_invitations"> & {
  invitations: PendingInvitation[];
};

type UserLifecycleAction = "disable" | "restore";

interface UserLifecycleTarget {
  user: AdminUser;
  action: UserLifecycleAction;
}

function formatDate(value: string) {
  return format(new Date(value), "MMM d, yyyy");
}

export default function UsersPage() {
  const router = useRouter();
  const { isAdmin, isLoading: isAuthLoading } = useAuth();
  const [createOpen, setCreateOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [inviteUrl, setInviteUrl] = useState("");
  const [copied, setCopied] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const inviteCreationGate = useRef(new RequestGate());
  const [lifecycleTarget, setLifecycleTarget] =
    useState<UserLifecycleTarget | null>(null);

  const {
    data: usersResponse,
    isLoading,
    error: usersError,
    refetch,
  } = useApiQuery<UsersResponse>(
    async () => {
      const response = await api.get<UsersTransportResponse>(
        USER_ENDPOINTS.BASE,
      );
      return {
        users: response.data.users,
        pending_invitations: response.data.invitations,
      };
    },
    { errorToast: "Failed to load users" },
  );

  useEffect(() => {
    if (!isAuthLoading && !isAdmin) {
      router.replace("/dashboard/memories");
    }
  }, [isAdmin, isAuthLoading, router]);

  useEffect(() => {
    const gate = new RequestGate();
    inviteCreationGate.current = gate;
    return () => gate.dispose();
  }, []);

  const users = usersResponse?.users ?? [];
  const invitations = usersResponse?.pending_invitations ?? [];
  const emailValid = isValidEmail(email);

  const handleDialogClose = (open: boolean) => {
    if (!open) {
      inviteCreationGate.current.invalidate();
      setInviteUrl("");
      setEmail("");
      setCopied(false);
      setIsCreating(false);
    }
    setCreateOpen(open);
  };

  const handleCreate = async () => {
    const request = inviteCreationGate.current.begin();
    if (request === null) return;
    setIsCreating(true);

    try {
      const response = await api.post<InvitationCreateResponse>(
        USER_ENDPOINTS.INVITATIONS,
        { email },
      );
      const disposition =
        inviteCreationGate.current.successDisposition(request);
      if (disposition.revealSecret) setInviteUrl(response.data.invite_url);
      if (disposition.refresh) void refetch();
    } catch (error) {
      if (!inviteCreationGate.current.isCurrent(request)) return;
      toast({
        title: "Failed to create invitation",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      if (inviteCreationGate.current.isCurrent(request)) {
        inviteCreationGate.current.finish(request);
        setIsCreating(false);
      }
    }
  };

  const handleRevoke = async (invitation: PendingInvitation) => {
    try {
      await api.delete(USER_ENDPOINTS.INVITATION_BY_ID(invitation.id));
      toast({ title: "Invitation revoked", variant: "success" });
      void refetch();
    } catch (error) {
      toast({
        title: "Failed to revoke invitation",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  const handleLifecycleAction = async () => {
    if (!lifecycleTarget) return;

    const { action, user } = lifecycleTarget;
    try {
      await api.post(
        action === "disable"
          ? USER_ENDPOINTS.DISABLE(user.id)
          : USER_ENDPOINTS.RESTORE(user.id),
      );
      toast({
        title: action === "disable" ? "User disabled" : "User restored",
        variant: "success",
      });
      setLifecycleTarget(null);
      void refetch();
    } catch (error) {
      toast({
        title: `Failed to ${action} user`,
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  const userColumns = [
    { key: "name" as keyof AdminUser, label: "Name", width: 160 },
    { key: "email" as keyof AdminUser, label: "Email", width: 220 },
    { key: "role" as keyof AdminUser, label: "Role", width: 90 },
    {
      key: "disabled_at" as keyof AdminUser,
      label: "Status",
      width: 100,
      render: (value: string | null) => (
        <Badge variant="outline">{value ? "Disabled" : "Active"}</Badge>
      ),
    },
    {
      key: "created_at" as keyof AdminUser,
      label: "Created",
      width: 120,
      render: (value: string) => formatDate(value),
    },
    {
      key: "id" as keyof AdminUser,
      label: "",
      width: 90,
      render: (_: string, row: AdminUser) =>
        row.role === "member" ? (
          <Button
            onClick={() =>
              setLifecycleTarget({
                user: row,
                action: row.disabled_at ? "restore" : "disable",
              })
            }
            size="sm"
            variant="outline"
          >
            {row.disabled_at ? (
              <UserRoundCheck className="mr-1 size-3.5" />
            ) : (
              <UserRoundX className="mr-1 size-3.5" />
            )}
            {row.disabled_at ? "Restore" : "Disable"}
          </Button>
        ) : null,
    },
  ];

  const invitationColumns = [
    { key: "email" as keyof PendingInvitation, label: "Email", width: 240 },
    {
      key: "created_at" as keyof PendingInvitation,
      label: "Created",
      width: 140,
      render: (value: string) => formatDate(value),
    },
    {
      key: "expires_at" as keyof PendingInvitation,
      label: "Expires",
      width: 140,
      render: (value: string) => formatDate(value),
    },
    {
      key: "id" as keyof PendingInvitation,
      label: "",
      width: 90,
      render: (_: string, row: PendingInvitation) => (
        <Button
          onClick={() => void handleRevoke(row)}
          size="sm"
          variant="outline"
        >
          <Trash2 className="mr-1 size-3.5 text-onSurface-danger-primary" />
          Revoke
        </Button>
      ),
    },
  ];

  if (!isAuthLoading && !isAdmin) {
    return null;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-fustat text-xl font-semibold">Users</h1>
          <p className="mt-1 text-sm text-onSurface-default-tertiary">
            Manage member access and invitations.
          </p>
        </div>
        <Dialog open={createOpen} onOpenChange={handleDialogClose}>
          <DialogTrigger asChild>
            <Button size="sm">
              <Plus className="mr-1 size-4" /> Invite user
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>
                {inviteUrl ? "Invitation created" : "Invite a user"}
              </DialogTitle>
            </DialogHeader>
            {!inviteUrl ? (
              <div className="mt-2 space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="invite-email">Email</Label>
                  <Input
                    autoFocus
                    id="invite-email"
                    onChange={(event) => setEmail(event.target.value)}
                    placeholder="member@example.com"
                    type="email"
                    value={email}
                  />
                </div>
                <Button
                  className="w-full"
                  disabled={!emailValid || isCreating}
                  onClick={() => void handleCreate()}
                >
                  {isCreating ? "Creating invitation..." : "Create invitation"}
                </Button>
              </div>
            ) : (
              <div className="mt-2 space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="invite-url">Invitation URL</Label>
                  <div className="flex gap-2">
                    <Input
                      id="invite-url"
                      readOnly
                      value={inviteUrl}
                      className="font-mono text-sm"
                    />
                    <CopyToClipboard
                      onCopy={() => {
                        setCopied(true);
                        setTimeout(() => setCopied(false), 2000);
                      }}
                      text={inviteUrl}
                    >
                      <Button
                        aria-label="Copy invitation URL"
                        size="icon"
                        variant="outline"
                      >
                        {copied ? (
                          <Check className="size-4" />
                        ) : (
                          <Copy className="size-4" />
                        )}
                      </Button>
                    </CopyToClipboard>
                  </div>
                  <p className="text-xs text-onSurface-danger-primary">
                    Copy this link now. It won&apos;t be shown again.
                  </p>
                </div>
                <Button
                  className="w-full"
                  onClick={() => handleDialogClose(false)}
                >
                  Done
                </Button>
              </div>
            )}
          </DialogContent>
        </Dialog>
      </div>

      {isLoading ? (
        <TableSkeleton columns={5} rows={4} />
      ) : usersError ? (
        <Card className="space-y-3 border-memBorder-primary p-6" role="alert">
          <p className="text-sm text-onSurface-danger-primary">{usersError}</p>
          <Button onClick={() => void refetch()} size="sm" variant="outline">
            Retry
          </Button>
        </Card>
      ) : (
        <>
          <section className="space-y-3">
            <h2 className="font-fustat text-base font-semibold">Users</h2>
            {users.length === 0 ? (
              <EmptyState
                description="Users will appear here after they accept an invitation."
                title="No users yet"
              />
            ) : (
              <Card className="overflow-hidden border-memBorder-primary">
                <DataTable
                  columns={userColumns}
                  data={users}
                  getRowKey={(row) => row.id}
                />
              </Card>
            )}
          </section>

          <section className="space-y-3">
            <h2 className="font-fustat text-base font-semibold">
              Pending invitations
            </h2>
            {invitations.length === 0 ? (
              <EmptyState
                description="Create an invitation to add a member."
                title="No pending invitations"
              />
            ) : (
              <Card className="overflow-hidden border-memBorder-primary">
                <DataTable
                  columns={invitationColumns}
                  data={invitations}
                  getRowKey={(row) => row.id}
                />
              </Card>
            )}
          </section>
        </>
      )}

      <DeleteConfirmationModal
        confirmButtonText={
          lifecycleTarget?.action === "disable" ? "Disable" : "Restore"
        }
        description={
          lifecycleTarget?.action === "disable"
            ? "This member will no longer be able to sign in."
            : "This member will be able to sign in again."
        }
        isOpen={!!lifecycleTarget}
        itemName={lifecycleTarget?.user.email ?? ""}
        onClose={() => setLifecycleTarget(null)}
        onConfirm={handleLifecycleAction}
        title={
          lifecycleTarget?.action === "disable"
            ? "Disable user"
            : "Restore user"
        }
      />
    </div>
  );
}
