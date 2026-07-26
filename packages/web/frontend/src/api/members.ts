import { apiGet, apiPost, apiDelete } from "./client";

export type Member = { user_id: number; username: string; role: string };
export type UserLite = { id: number; username: string; role: string };

const enc = encodeURIComponent;

export const getMembers = (ws: string) =>
  apiGet<{ members: Member[] }>(`/workspaces/${enc(ws)}/members`);
export const addMember = (ws: string, username: string, role: string = "member") =>
  apiPost(`/workspaces/${enc(ws)}/members`, { username, role });
export const removeMember = (ws: string, username: string) =>
  apiDelete(`/workspaces/${enc(ws)}/members/${enc(username)}`);
export const listUsers = () => apiGet<{ users: UserLite[] }>("/users");
