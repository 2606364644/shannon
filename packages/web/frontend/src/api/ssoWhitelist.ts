import { apiGet, apiPost, apiDelete } from "./client";

// ── SSO 登录白名单（spec 2026-08-25 §5.2，admin-only）────────────────────────
export type SsoWhitelistRow = { nick: string; added_by: string | null; created_at: string };

// enabled=白名单运行时开关现值（Task 10，无行/缺字段默认开）
export const getSsoWhitelist = () =>
  apiGet<{ whitelist: SsoWhitelistRow[]; enabled: boolean }>("/auth/sso/whitelist");
export const addSsoWhitelist = (nick: string) =>
  apiPost<{ ok: boolean }>("/auth/sso/whitelist", { nick });
export const removeSsoWhitelist = (nick: string) =>
  apiDelete<{ ok: boolean }>(`/auth/sso/whitelist/${encodeURIComponent(nick)}`);
export const setWhitelistEnabled = (enabled: boolean) =>
  apiPost<{ ok: boolean; enabled: boolean }>("/auth/sso/whitelist/enabled", { enabled });
