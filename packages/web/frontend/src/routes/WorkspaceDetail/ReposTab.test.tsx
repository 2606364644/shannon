import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "@/auth/AuthContext";
import { ReposTab } from "./ReposTab";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

describe("ReposTab", () => {
  it("列出 ws 内仓库", async () => {
    const fm = vi.spyOn(window, "fetch");
    // AuthProvider 首拉 /auth/me（user 解析成功 → 后续 ReposTab 的 listRepos 才会发起）
    fm.mockResolvedValueOnce(
      new Response(JSON.stringify({ user: { id: 1, username: "alice", role: "user" } }), { status: 200 }),
    );
    // 之后所有 fetch（listRepos("ws1")）返回仓库列表
    fm.mockResolvedValue(
      new Response(JSON.stringify([{ name: "r1", state: "ready" }]), { status: 200 }),
    );
    render(
      <AuthProvider>
        <MemoryRouter>
          <ReposTab workspace="ws1" />
        </MemoryRouter>
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByText("r1")).toBeTruthy());
    // "添加仓库" 入口存在（i18n mock → key 字符串）
    expect(screen.getByText("repos.addRepo")).toBeTruthy();
  });

  it("关联仓库：显关联徽标、无更新按钮、删除按钮为「取消关联」", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockResolvedValueOnce(
      new Response(JSON.stringify({ user: { id: 1, username: "alice", role: "user" } }), { status: 200 }),
    );
    fm.mockResolvedValue(
      new Response(JSON.stringify([
        { name: "ftoa", linked: true, state: "ready", source: { kind: "linked" } },
      ]), { status: 200 }),
    );
    render(
      <AuthProvider><MemoryRouter><ReposTab workspace="ws1" /></MemoryRouter></AuthProvider>,
    );
    await waitFor(() => expect(screen.getByText("ftoa")).toBeTruthy());
    expect(screen.getByText("repos.linkedBadge")).toBeTruthy();   // 关联徽标
    expect(screen.queryByText("common.update")).toBeNull();        // 无更新(pull)按钮
    expect(screen.getByText("repos.unlink")).toBeTruthy();         // 删除→取消关联
  });

  it("私有克隆：有更新按钮、删除按钮为「删除」、无关联徽标", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockResolvedValueOnce(
      new Response(JSON.stringify({ user: { id: 1, username: "alice", role: "user" } }), { status: 200 }),
    );
    fm.mockResolvedValue(
      new Response(JSON.stringify([{ name: "r1", state: "ready" }]), { status: 200 }),
    );
    render(
      <AuthProvider><MemoryRouter><ReposTab workspace="ws1" /></MemoryRouter></AuthProvider>,
    );
    await waitFor(() => expect(screen.getByText("r1")).toBeTruthy());
    expect(screen.queryByText("repos.linkedBadge")).toBeNull();
    expect(screen.getByText("common.update")).toBeTruthy();
    expect(screen.getByText("common.delete")).toBeTruthy();
  });
});
