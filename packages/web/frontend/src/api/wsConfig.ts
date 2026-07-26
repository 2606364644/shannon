import { apiGet, apiPut } from "./client";

export interface WsProviderFields {
  ai_provider: string | null;
  api_key: string | null; // GET 返 "••••"（已配置）或 null
  base_url: string | null;
  model: string | null;
  small_model: string | null;
  medium_model: string | null;
  large_model: string | null;
  max_turns: number | null;
  adaptive_thinking: boolean | null;
}
export interface WsGitFields {
  gitlab_user: string | null;
  gitlab_token: string | null; // GET 返 "••••"（已配置）或 null
}
export interface WsConfig {
  provider: WsProviderFields;
  git?: WsGitFields; // P3c 阶段 4
}
export type WsConfigInput = {
  provider: Partial<WsProviderFields>;
  git?: { gitlab_user?: string | null; gitlab_token?: string | null };
};

const enc = encodeURIComponent;

export const getWsConfig = (ws: string) =>
  apiGet<WsConfig>(`/workspaces/${enc(ws)}/config`);
export const putWsConfig = (ws: string, body: WsConfigInput) =>
  apiPut(`/workspaces/${enc(ws)}/config`, body);
