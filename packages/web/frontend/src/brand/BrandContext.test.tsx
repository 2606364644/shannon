import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import { BrandProvider } from "@/brand/BrandContext";

// 模拟未登录 / Loading：system-status 拿不到 brand（data 恒 null，如 /login 页 401）。
vi.mock("@/api/systemStatus", () => ({
  useSystemStatus: () => ({ data: null, loading: true, error: null, refresh: async () => {} }),
}));

describe("BrandContext", () => {
  beforeEach(() => {
    document.title = "";
  });

  it("初始 brand 继承后端注入的 document.title，Loading 期不覆盖回默认 Supernova", () => {
    // 模拟生产 index.html：后端已把生效品牌名注入 <title>
    document.title = "ft-codesec";
    render(
      <BrandProvider>
        <span>x</span>
      </BrandProvider>,
    );
    // React 挂载后 document.title 应保持注入值，不被 DEFAULT_BRAND "Supernova" 覆盖
    expect(document.title).toBe("ft-codesec");
  });

  it("document.title 为空时回落默认 Supernova（无注入兜底）", () => {
    render(
      <BrandProvider>
        <span>x</span>
      </BrandProvider>,
    );
    expect(document.title).toBe("Supernova");
  });
});
