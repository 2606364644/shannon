import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SWRConfig } from "swr";
import { AuthProvider } from "@/auth/AuthContext";
import { ReposTab } from "./ReposTab";

vi.mock("react-i18next", () => {
  // t 引用必须在多次渲染间稳定：ReposTab 的 refresh = useCallback([t, workspace]) +
  // useEffect([refresh, user]) 依赖链，若 t 每次新引用 → refresh 每次新 → useEffect 无限重跑
  // → listRepos 无限调用 → event loop 饥饿、waitFor 永不 resolve。工厂内定义一次、复用。
  const t = (k: string) => k;
  return { useTranslation: () => ({ t }) };
});
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() } }));

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
        <SWRConfig value={{ provider: () => new Map() }}>
          <MemoryRouter>
            <ReposTab workspace="ws1" />
          </MemoryRouter>
        </SWRConfig>
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByText("r1")).toBeTruthy());
    // "新建仓库" 入口存在（i18n mock → key 字符串）
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
      <AuthProvider><SWRConfig value={{ provider: () => new Map() }}><MemoryRouter><ReposTab workspace="ws1" /></MemoryRouter></SWRConfig></AuthProvider>,
    );
    await waitFor(() => expect(screen.getByText("ftoa")).toBeTruthy());
    expect(screen.getByText("repos.linkedBadge")).toBeTruthy();   // 关联徽标
    expect(screen.queryByLabelText("repos.updateAria")).toBeNull();  // 无更新(pull)按钮（icon-only）
    expect(screen.getByLabelText("repos.unlinkAria")).toBeTruthy(); // 取消关联（icon-only，文字在 tooltip）
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
      <AuthProvider><SWRConfig value={{ provider: () => new Map() }}><MemoryRouter><ReposTab workspace="ws1" /></MemoryRouter></SWRConfig></AuthProvider>,
    );
    await waitFor(() => expect(screen.getByText("r1")).toBeTruthy());
    expect(screen.queryByText("repos.linkedBadge")).toBeNull();
    expect(screen.getByLabelText("repos.updateAria")).toBeTruthy();  // icon-only 更新
    expect(screen.getByLabelText("repos.deleteAria")).toBeTruthy();  // icon-only 删除
  });

  it("批量删除：勾全选 → 删除选中 → 确认 → POST batch-delete 带 names 并刷新", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockImplementation(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url.includes("/auth/me")) {
        return new Response(JSON.stringify({ user: { id: 1, username: "alice", role: "user" } }), { status: 200 });
      }
      if (url.includes("/repos/batch-delete")) {
        return new Response(JSON.stringify({ deleted: ["r1", "r2"], unlinked: [], skipped: [] }), { status: 200 });
      }
      return new Response(JSON.stringify([
        { name: "r1", state: "ready" }, { name: "r2", state: "ready" },
      ]), { status: 200 });
    });
    render(
      <AuthProvider><SWRConfig value={{ provider: () => new Map() }}><MemoryRouter><ReposTab workspace="ws1" /></MemoryRouter></SWRConfig></AuthProvider>,
    );
    await waitFor(() => expect(screen.getByText("r1")).toBeTruthy());
    // 初始无批量删除入口
    expect(screen.queryByText("repos.bulk.deleteSelected")).toBeNull();
    // 勾全选（名称列头 checkbox）
    fireEvent.click(screen.getByLabelText("repos.bulk.selectAll"));
    // 批量操作栏出现
    expect(screen.getByText("repos.bulk.deleteSelected")).toBeTruthy();
    // 点「删除选中」→「确认」
    fireEvent.click(screen.getByText("repos.bulk.deleteSelected"));
    fireEvent.click(screen.getByText("common.confirm"));
    // POST /repos/batch-delete 被调用，body.names 含 r1/r2
    await waitFor(() => {
      expect(fm.mock.calls.some(([u]) => String(u).includes("/repos/batch-delete"))).toBeTruthy();
    });
    const batchCall = fm.mock.calls.find(([u]) => String(u).includes("/repos/batch-delete"));
    const init = batchCall?.[1] as RequestInit | undefined;
    const names: string[] = JSON.parse(init?.body as string).names;
    expect([...names].sort()).toEqual(["r1", "r2"]);
  });

  it("扁平列表：列头唯一、单个 table、所在目录独立成列", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockImplementation(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url.includes("/auth/me")) {
        return new Response(JSON.stringify({ user: { id: 1, username: "alice", role: "user" } }), { status: 200 });
      }
      return new Response(JSON.stringify([
        { name: "alpha/r1", group: "alpha", state: "ready" },
        { name: "beta/r2", group: "beta", state: "ready" },
      ]), { status: 200 });
    });
    const { container } = render(
      <AuthProvider><SWRConfig value={{ provider: () => new Map() }}><MemoryRouter><ReposTab workspace="ws1" /></MemoryRouter></SWRConfig></AuthProvider>,
    );
    await waitFor(() => expect(screen.getByText("r1")).toBeTruthy());
    // 列头只出现一次（重构前每个分组一张独立表，列头重复 N 次）
    expect(screen.getAllByText("repos.table.name")).toHaveLength(1);
    // 整页只有一个 table 元素（重构前 N 分组 = N 张表）
    expect(container.querySelectorAll("table")).toHaveLength(1);
    // 所在目录列头存在
    expect(screen.getByText("repos.table.directory")).toBeTruthy();
    // 目录名落在「目录」列（扁平列表，不再有分组行）
    expect(screen.getByText("alpha")).toBeTruthy();
    expect(screen.getByText("beta")).toBeTruthy();
  });

  it("空壳目录：显示 empty 徽标、无更新按钮、有删除按钮（占位可清理）", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockImplementation(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url.includes("/auth/me")) {
        return new Response(JSON.stringify({ user: { id: 1, username: "alice", role: "user" } }), { status: 200 });
      }
      return new Response(JSON.stringify([
        { name: "frontend", state: "empty", source: { kind: "unknown" } },
      ]), { status: 200 });
    });
    render(
      <AuthProvider><SWRConfig value={{ provider: () => new Map() }}><MemoryRouter><ReposTab workspace="ws1" /></MemoryRouter></SWRConfig></AuthProvider>,
    );
    await waitFor(() => expect(screen.getByText("frontend")).toBeTruthy());
    expect(screen.getByText("repos.states.empty")).toBeTruthy();     // 空目录徽标（i18n mock → key）
    expect(screen.queryByLabelText("repos.updateAria")).toBeNull(); // 无更新(pull)按钮——空壳 pull 必 409
    expect(screen.getByLabelText("repos.deleteAria")).toBeTruthy(); // 有删除按钮（icon-only）
  });
});
