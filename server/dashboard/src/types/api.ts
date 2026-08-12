export interface Memory {
  id: string;
  memory: string;
  user_id?: string;
  agent_id?: string;
  created_at?: string;
  updated_at?: string;
  categories: string[] | null;
  category_status: "pending" | "completed" | "failed" | "unclassified";
}

export interface CategoryDefinition {
  name: string;
  description: string;
}

export interface CategoryCount {
  name: string;
  count: number;
}

export interface CategoryCatalogResponse {
  saved: CategoryDefinition[];
  active: CategoryDefinition[];
  source: "defaults" | "user";
  counts: Record<string, number>;
  retired: CategoryCount[];
}

export interface CategoryJob {
  id: string;
  memory_id: string;
  state:
    | "queued"
    | "processing"
    | "retrying"
    | "completed"
    | "failed"
    | "cancelled";
  attempts: number;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  next_attempt_at: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface ReclassificationPreview {
  scope: "unclassified_failed" | "all";
  eligible_memories: number;
  estimated_calls: number;
  estimated_input_tokens: number;
  estimated_output_tokens: number;
  estimated_cost: number | null;
}

export interface ReclassificationStartResponse {
  created_jobs: number;
  skipped_active_jobs: number;
  eligible_memories: number;
}

export interface ApiKey {
  id: string;
  label: string;
  key_prefix: string;
  created_at: string;
  last_used_at: string | null;
}

export interface ApiKeyCreateResponse {
  id: string;
  label: string;
  key: string;
  key_prefix: string;
  created_at: string;
}

export interface AdminUser {
  id: string;
  name: string;
  email: string;
  role: "admin" | "member";
  created_at: string;
  disabled_at: string | null;
}

export interface PendingInvitation {
  id: string;
  email: string;
  created_at: string;
  expires_at: string;
  status: "pending" | "expired";
}

export interface UsersResponse {
  users: AdminUser[];
  pending_invitations: PendingInvitation[];
}

export interface InvitationCreateResponse {
  id: string;
  email: string;
  expires_at: string;
  invite_url: string;
}

export interface ApiRequestLog {
  id: string;
  created_at: string;
  method: string;
  path: string;
  status_code: number;
  latency_ms: number;
  auth_type: string;
}

export type EntityType = "user" | "agent" | "run";

export interface Entity {
  id: string;
  type: EntityType;
  total_memories: number;
  created_at: string | null;
  updated_at: string | null;
}
