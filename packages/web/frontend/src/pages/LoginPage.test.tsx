import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { AuthProvider } from "@/auth/AuthContext";
import i18n from "@/i18n";
import LoginPage from "./LoginPage";

function wrap() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={["/login"]}>
        <Routes><Route path="/login" element={<LoginPage />} /></Routes>
      </MemoryRouter>
    </AuthProvider>
  );
}

describe("LoginPage", () => {
  // jsdom navigator.language 默认 en-US，i18n LanguageDetector 会渲染英文，
  // 本测试断言中文文案，故每个测试钉回 zh（遵循 App.test.tsx / WorkspaceListPage.test.tsx 既有模式）。
  beforeEach(() => i18n.changeLanguage("zh"));

  it("渲染欢迎标题与表单（未登录态）", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 401 })); // /auth/me 401
    wrap();
    await waitFor(() => expect(screen.getByText("欢迎回来")).toBeTruthy());
    expect(screen.getByLabelText("用户名")).toBeTruthy();
    expect(screen.getByLabelText("密码")).toBeTruthy();
    expect(screen.getByRole("button", { name: "登录" })).toBeTruthy();
  });
});
