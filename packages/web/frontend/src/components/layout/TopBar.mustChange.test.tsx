import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { TopBar } from "./TopBar";
import i18n from "@/i18n";

// 本文件聚焦 must_change_password badge：stub useAuth 注入 must_change=true 的 user。
// TopBar 主行为测试见 TopBar.test.tsx（此处独立文件避免 vi.mock 文件级提升冲突）。
let mockUser: { id: number; username: string; role: string; must_change_password: boolean } = {
  id: 1, username: "admin", role: "admin", must_change_password: true,
};
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: mockUser,
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));
// TopBar 含 ThemeToggle（消费 useTheme）；本文件聚焦 badge 不测主题，stub useTheme
// 避免必须 ThemeProvider（同 TopBar.test.tsx:13）。静态返回、不引用提升变量，不与 auth mock 冲突。
vi.mock("@/lib/theme-context", () => ({ useTheme: () => ({ theme: "dark", setTheme: vi.fn() }) }));

function renderTopBar() {
  return render(
    <MemoryRouter>
      <TopBar />
    </MemoryRouter>,
  );
}

describe("TopBar must_change_password badge", () => {
  beforeEach(() => {
    i18n.changeLanguage("zh");
    mockUser = { id: 1, username: "admin", role: "admin", must_change_password: true };
  });

  it("must_change=true 显示 ⚠ badge 按钮", () => {
    renderTopBar();
    expect(screen.getByTestId("must-change-badge")).toBeInTheDocument();
  });

  it("must_change=false 不显示 badge", () => {
    mockUser = { ...mockUser, must_change_password: false };
    renderTopBar();
    expect(screen.queryByTestId("must-change-badge")).toBeNull();
  });

  it("点击 badge 调 onOpenChangePwd 回调", async () => {
    const onOpen = vi.fn();
    render(
      <MemoryRouter>
        <TopBar onOpenChangePwd={onOpen} />
      </MemoryRouter>,
    );
    const badge = screen.getByTestId("must-change-badge");
    await act(async () => {
      badge.click();
    });
    expect(onOpen).toHaveBeenCalled();
  });
});
