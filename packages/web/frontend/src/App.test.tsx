import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { router } from "./router";
import App from "./App";
import { Toaster } from "@/components/ui/sonner";
import i18n from "@/i18n";

// jsdom 默认 location.pathname = "/"，createBrowserRouter 读 History API → 落地根路由。
// BrandProvider(根)启动时拉 /api/system-status 取 brand_name,这里一并 mock 避免警告。
// Task 16: 业务路由组已被 RequireAuth 包裹，AuthProvider 拉 /api/auth/me 必须返已登录用户，
// 否则 RequireAuth 跳 /login，根路由不再渲染 DashboardPage。
const server = setupServer(
  http.get("/api/workspaces", () => HttpResponse.json([])),
  // /api/scans 必须显式 mock：DashboardPage 此前把加载失败静默吞掉才落到空态，
  // 错误态分支上线后未 mock 的请求会正确显示「加载失败」而非「还没有扫描」。
  http.get("/api/scans", () => HttpResponse.json([])),
  http.get("/api/system-status", () => HttpResponse.json({ brand_name: "Supernova" })),
  http.get("/api/auth/me", () =>
    HttpResponse.json({ user: { id: 1, username: "tester", role: "user" } }),
  ),
);
beforeAll(() => {
  server.listen();
  // jsdom navigator.language 默认 en-US → LanguageDetector 渲染英文，本测试断言中文文案，
  // 故钉死 zh 使断言确定（test-setup.ts 已在 import 期初始化 i18n 单例，此处复用并切语言）。
  void i18n.changeLanguage("zh");
});
afterAll(() => server.close());

describe("App 集成冒烟", () => {
  it("根路由渲染 DashboardPage（main 内含空态提示；子项目5 Task3 改根路由）", async () => {
    render(<App />);
    // Task 16: 业务路由组已包 RequireAuth，AuthProvider 拉 /auth/me 完成 + 通过守卫后 AppShell 才挂，
    // 故用 findByRole（异步）等 main 出现，而非原同步 getByRole。
    const main = await screen.findByRole("main");
    await waitFor(() => {
      expect(within(main).getByText(/还没有扫描/i)).toBeInTheDocument();
    });
  });
  it("RouterProvider 可用（导出 router）", () => {
    expect(router).toBeDefined();
  });
});

describe("App Toaster 挂载", () => {
  it("App 根挂 <Toaster />（toast 通道）", () => {
    render(<App />);
    // sonner <Toaster /> 默认渲染 <section aria-label="Notifications"> 到 body
    expect(screen.getByLabelText(/notifications/i)).toBeInTheDocument();
  });
  it("Toaster 组件可独立渲染（导出可用）", () => {
    render(<Toaster />);
    expect(screen.getByLabelText(/notifications/i)).toBeInTheDocument();
  });
});
