import { apiGet, apiPut, apiDelete } from "./client";

// 模型定价两级管理（spec 2026-08-28 §4.2）：全局表（admin）+ 工作区覆盖（manager）。
// source 标注每个价来自哪层（内置 / profile env / 全局 / 本工作区），供来源徽章渲染。
export type PricingSource = "builtin" | "profile_env" | "global" | "workspace";

// 4 档价格（单位：本币 / 百万 token）
// currency（可选，2026-08-28）：模型级币种覆盖；null/缺省 = 跟随表级默认。
export interface Prices {
  input: number;
  output: number;
  cache_read: number;
  cache_creation: number;
  currency?: string | null;
}

export interface PricingRow {
  model: string;
  prices: Prices;
  source: PricingSource;
  /** 行级币种（兄弟字段，不进 prices）：null = 跟随表级默认（后端不 resolve，保住跟随语义） */
  currency?: string | null;
}

/** GET /api/pricing —— 全局视角（不含工作区层）。 */
export interface PricingView {
  currency: string;
  models: PricingRow[];
  has_global_table: boolean;
  builtin_defaults: Record<string, Prices>;
  table_corrupt?: boolean;
}

/** GET /api/workspaces/{ws}/pricing —— 含工作区层来源。 */
export interface WsPricingView {
  currency: string;
  models: PricingRow[];
  override_exists: boolean;
  builtin_defaults: Record<string, Prices>;
  table_corrupt?: boolean;
}

const enc = encodeURIComponent;

export const getPricing = () => apiGet<PricingView>("/pricing");
export const putPricing = (currency: string, models: Record<string, Prices>) =>
  apiPut<{ ok: boolean }>("/pricing", { currency, models });
export const deletePricing = () => apiDelete<{ ok: boolean }>("/pricing");

export const getWsPricing = (ws: string) =>
  apiGet<WsPricingView>(`/workspaces/${enc(ws)}/pricing`);
export const putWsPricing = (ws: string, currency: string, models: Record<string, Prices>) =>
  apiPut<{ ok: boolean }>(`/workspaces/${enc(ws)}/pricing`, { currency, models });
export const deleteWsPricing = (ws: string) =>
  apiDelete<{ ok: boolean }>(`/workspaces/${enc(ws)}/pricing`);
