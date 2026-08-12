export const AUTH_ENDPOINTS = {
  SETUP_STATUS: "/auth/setup-status",
  REGISTER: "/auth/register",
  LOGIN: "/auth/login",
  ACCEPT_INVITATION: "/auth/invitations/accept",
  REFRESH: "/auth/refresh",
  ME: "/auth/me",
  CHANGE_PASSWORD: "/auth/change-password",
  ONBOARDING_COMPLETE: "/auth/onboarding-complete",
} as const;

export const MEMORY_ENDPOINTS = {
  BASE: "/memories",
  BY_ID: (memoryId: string) => `/memories/${memoryId}`,
  HISTORY: (memoryId: string) => `/memories/${memoryId}/history`,
  CONFIGURE: "/configure",
  CONFIGURE_PROVIDERS: "/configure/providers",
  RESET: "/reset",
  GENERATE_INSTRUCTIONS: "/generate-instructions",
} as const;

export const API_KEY_ENDPOINTS = {
  BASE: "/api-keys",
  BY_ID: (keyId: string) => `/api-keys/${keyId}`,
} as const;

export const USER_ENDPOINTS = {
  BASE: "/admin/users",
  INVITATIONS: "/admin/invitations",
  INVITATION_BY_ID: (id: string) => `/admin/invitations/${id}`,
  DISABLE: (id: string) => `/admin/users/${id}/disable`,
  RESTORE: (id: string) => `/admin/users/${id}/restore`,
} as const;

export const REQUEST_ENDPOINTS = {
  BASE: "/requests",
} as const;

export const ENTITY_ENDPOINTS = {
  BASE: "/entities",
  BY_ID: (type: string, id: string) =>
    `/entities/${type}/${encodeURIComponent(id)}`,
} as const;

export const CATEGORY_ENDPOINTS = {
  BASE: "/categories",
  BY_NAME: (name: string) => `/categories/${encodeURIComponent(name)}`,
  JOBS: "/categories/jobs",
  PREVIEW: "/categories/reclassify/preview",
  EXECUTE: "/categories/reclassify",
} as const;
