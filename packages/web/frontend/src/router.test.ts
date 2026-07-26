import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const router = readFileSync(resolve(__dirname, "router.tsx"), "utf8");

describe("router.tsx 结构", () => {
  it("根包 <RequireAuth><AppShell /></RequireAuth>", () => {
    expect(router).toContain("AppShell");
    // Task 16: 业务路由组 AppShell 包 RequireAuth 路由守卫
    expect(router).toMatch(/element:\s*<RequireAuth>\s*<AppShell/);
    expect(router).toContain('import { RequireAuth }');
  });
  it("dev 预览页 dev-only 守卫（import.meta.env.DEV）", () => {
    expect(router).toContain("import.meta.env.DEV");
    expect(router).toContain("DevComponentsPage");
    expect(router).toContain("/dev/components");
  });
  it("保留现有业务路由", () => {
    expect(router).toContain("WorkspaceListPage");
    expect(router).toContain("ScanNewPage");
    expect(router).toContain("WorkspaceDetail");
    expect(router).toContain("DefaultTab");
  });
  it("子项目5:Dashboard/Settings 路由 + Workspaces 迁 /workspaces", () => {
    expect(router).toContain("DashboardPage");
    expect(router).toContain("SettingsPage");
    expect(router).toContain('"/workspaces"');
    expect(router).toContain('"/settings"');
  });
});
