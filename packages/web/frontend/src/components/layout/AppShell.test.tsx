import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "./AppShell";

// AppShell 含 TopBar（T17 起 TopBar 集成 UserMenu，UserMenu 用 useAuth）。
// 本测试聚焦 AppShell 渲染 TopBar + Outlet，不测 auth，故 stub useAuth 注入固定 user
// 避免 "useAuth 必须在 AuthProvider 内使用"。
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 1, username: "alice", role: "user" },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));
// AppShell 含 TopBar → ThemeToggle（消费 useTheme）；本测试聚焦布局不测主题，stub useTheme。
vi.mock("@/lib/theme-context", () => ({
  useTheme: () => ({ theme: "dark", setTheme: vi.fn() }),
}));

describe("AppShell", () => {
  it("渲染 TopBar + Outlet 内容", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<div>page-content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    );
    expect(screen.getByText(/Supernova/i)).toBeInTheDocument();
    expect(screen.getByText("page-content")).toBeInTheDocument();
  });
});
