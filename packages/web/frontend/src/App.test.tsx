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
const server = setupServer(
  http.get("/api/workspaces", () => HttpResponse.json([])),
  http.get("/api/system-status", () => HttpResponse.json({ brand_name: "Supernova" })),
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
    const main = screen.getByRole("main");
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
