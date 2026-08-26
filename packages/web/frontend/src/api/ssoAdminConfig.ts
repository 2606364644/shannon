import { apiGet, apiPut } from "./client";

// ── SSO 运行时配置（spec 2026-08-26 §7.1，admin-only；设置页配置卡用）──────────
export type SsoAdminConfig = {
  enabled: boolean;
  auth_domain: string;
  public_base_url: string; // 原始配置值（可空）；空时后端运行时回落 https://{auth_domain}
  passport_base: string;
  session_ttl_hours: number;
  updated_at: string;
  updated_by: string;
};

export type SsoAdminConfigInput = Omit<SsoAdminConfig, "updated_at" | "updated_by">;

export const getSsoAdminConfig = () => apiGet<SsoAdminConfig>("/auth/sso/admin/config");

/** 全量更新 5 项，即时生效（无需重启）。400=校验失败（domain 必填/https/ttl 范围）。 */
export const updateSsoAdminConfig = (body: SsoAdminConfigInput) =>
  apiPut<SsoAdminConfig>("/auth/sso/admin/config", body);
