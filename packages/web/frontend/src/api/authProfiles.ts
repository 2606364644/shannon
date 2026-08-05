// 认证档案库 client（auth-profile-vault, Task 10 契约）。
// Pattern B：mirror members.ts —— 顶部 import helper + const enc = encodeURIComponent；
// 每个端点一个 arrow const，路径参数经 enc 单段 encode。
//
// 后端契约（plan auth-profile-vault）：
//   GET    /workspaces/{ws}/auth-profiles                          -> AuthProfile[]
//   GET    /workspaces/{ws}/auth-profiles/{pid}                    -> AuthProfile
//   POST   /workspaces/{ws}/auth-profiles                          -> AuthProfile            (body: Partial<AuthProfile>)
//   PUT    /workspaces/{ws}/auth-profiles/{pid}                    -> { ok: true }           (body: Partial<AuthProfile>)
//   DELETE /workspaces/{ws}/auth-profiles/{pid}                    -> { ok: true }
//   POST   /workspaces/{ws}/auth-profiles/{pid}/credentials/{cid}/test  -> { workflow_id, probe_dir }
//   GET    /workspaces/{ws}/auth-profiles/{pid}/credentials/{cid}/verify-status?workflow_id&probe_dir -> VerifyStatus
import { apiGet, apiPost, apiPut, apiDelete } from "./client";
import type { AuthProfile, VerifyStatus } from "./types";

const enc = encodeURIComponent;

export const listAuthProfiles = (ws: string) =>
  apiGet<AuthProfile[]>(`/workspaces/${enc(ws)}/auth-profiles`);
export const getAuthProfile = (ws: string, pid: string) =>
  apiGet<AuthProfile>(`/workspaces/${enc(ws)}/auth-profiles/${enc(pid)}`);
export const createAuthProfile = (ws: string, body: Partial<AuthProfile>) =>
  apiPost<AuthProfile>(`/workspaces/${enc(ws)}/auth-profiles`, body);
export const updateAuthProfile = (ws: string, pid: string, body: Partial<AuthProfile>) =>
  apiPut<{ ok: true }>(`/workspaces/${enc(ws)}/auth-profiles/${enc(pid)}`, body);
export const deleteAuthProfile = (ws: string, pid: string) =>
  apiDelete<{ ok: true }>(`/workspaces/${enc(ws)}/auth-profiles/${enc(pid)}`);
export const testCredential = (ws: string, pid: string, cid: string) =>
  apiPost<{ workflow_id: string; probe_dir: string }>(
    `/workspaces/${enc(ws)}/auth-profiles/${enc(pid)}/credentials/${enc(cid)}/test`, {});
export const getVerifyStatus = (
  ws: string, pid: string, cid: string, workflowId: string, probeDir: string,
) =>
  apiGet<VerifyStatus>(
    `/workspaces/${enc(ws)}/auth-profiles/${enc(pid)}/credentials/${enc(cid)}/verify-status`
    + `?workflow_id=${enc(workflowId)}&probe_dir=${enc(probeDir)}`);
