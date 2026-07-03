import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const router = readFileSync(resolve(__dirname, "router.tsx"), "utf8");

describe("router.tsx 结构", () => {
  it("根包 <AppShell />", () => {
    expect(router).toContain("AppShell");
    expect(router).toMatch(/element:\s*<AppShell/);
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
});
