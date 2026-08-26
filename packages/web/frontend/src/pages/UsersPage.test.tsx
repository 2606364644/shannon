import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { UsersPage } from "./UsersPage";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 1, username: "admin", role: "admin", must_change_password: false } }),
}));

function renderPage() {
  return render(<MemoryRouter><UsersPage /></MemoryRouter>);
}

describe("UsersPage", () => {
  beforeEach(() => {
    // 白名单面板已迁 SettingsPage（spec 2026-08-26），本页不再发 SSO 请求，
    // fetch 只需返回用户列表（mockImplementation 每次 new Response，body 只能读一次）。
    vi.spyOn(window, "fetch").mockImplementation(() => {
      const body = {
        users: [
          { id: 1, username: "admin", role: "admin", must_change_password: false, created_at: "2026-07-27T00:00:00Z" },
          { id: 2, username: "alice", role: "user", must_change_password: true, created_at: "2026-07-27T00:00:00Z" },
        ],
      };
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
    });
  });

  it("加载并渲染用户表格", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("admin")).toBeInTheDocument());
    expect(screen.getByText("alice")).toBeInTheDocument();
  });

  it("must_change 用户显示标记", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("alice")).toBeInTheDocument());
    // 表头列名与 badge 同 key -> 用 within 限定到 alice 行（避免与 <th> 碰撞）
    const aliceRow = screen.getByTestId("user-row-alice");
    expect(within(aliceRow).getByText("users.mustChange")).toBeInTheDocument();
    const adminRow = screen.getByTestId("user-row-admin");
    expect(within(adminRow).queryByText("users.mustChange")).toBeNull();
  });

  it("加载失败显错误态", async () => {
    // 同 beforeEach：每次调用 new Response（body 单次消费），保持「所有请求 500」语义
    vi.spyOn(window, "fetch").mockImplementation(() =>
      Promise.resolve(new Response("err", { status: 500 })));
    renderPage();
    await waitFor(() => expect(screen.getByText("users.loadFailed")).toBeInTheDocument());
  });
});
