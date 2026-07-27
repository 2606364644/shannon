import { apiGet, apiPost, apiPatch, apiDelete } from "./client";

export type UserRow = {
  id: number;
  username: string;
  role: "admin" | "user";
  must_change_password: boolean;
  created_at: string;
};

export type UserWorkspace = { workspace: string; role: "manager" | "member" };

export const listUsers = () => apiGet<{ users: UserRow[] }>("/users");
export const createUser = (body: { username: string; password: string; role: "admin" | "user" }) =>
  apiPost<{ user: UserRow }>("/users", body);
export const deleteUser = (id: number) => apiDelete(`/users/${id}`);
export const updateRole = (id: number, role: "admin" | "user") =>
  apiPatch(`/users/${id}`, { role });
export const resetPassword = (id: number, new_password: string) =>
  apiPost<{ ok: true }>(`/users/${id}/reset-password`, { new_password });
export const getUserWorkspaces = (id: number) =>
  apiGet<{ workspaces: UserWorkspace[] }>(`/users/${id}/workspaces`);
