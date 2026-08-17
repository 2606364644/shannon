import { apiGet, apiPut } from "./client";

// ws 配置 = env 文本（KEY=value）。config.yaml 是后端 SSOT，前端只见 env 文本。
// spec: docs/superpowers/specs/2026-08-10-ws-config-env-textarea-design.md
export interface WsConfigResponse {
  env_text: string;
  // 工作区尚无 config.yaml（未保存过）→ 前端据此预填完整推荐模板。
  is_default: boolean;
}
export interface WsConfigWarnings {
  ineffective: string[]; // 进程级 key（ws 不生效，需全局配）
  unknown: string[]; // 未知 key
}
export interface WsConfigPutResult {
  ok: boolean;
  warnings: WsConfigWarnings;
  // 保存成功即回显的原样文本（凭据已打码）——与后续 GET 返回一致
  env_text: string;
}

const enc = encodeURIComponent;

export const getWsConfig = (ws: string) =>
  apiGet<WsConfigResponse>(`/workspaces/${enc(ws)}/config`);
export const putWsConfig = (ws: string, env_text: string) =>
  apiPut<WsConfigPutResult>(`/workspaces/${enc(ws)}/config`, { env_text });
