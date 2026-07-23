import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// sticky z-index 栈不变量（spec §3.1 / §5-I2）：
//   弹窗 z-50 > TopBar z-40 > Tabs z-30（findings 浮动条已移除，z-20 层不再存在）
// 导航层必须低于弹窗，否则 dialog/popover/tooltip/select 会被吸顶 header 遮挡。
// 本测试读源码字符串（同 styles/tokens.test.ts 风格），防后人改 className 时回潮。
const SRC = resolve(__dirname, "..");
const topbar = readFileSync(resolve(SRC, "components/layout/TopBar.tsx"), "utf8");
const wd = readFileSync(resolve(SRC, "routes/WorkspaceDetail/index.tsx"), "utf8");
const dialog = readFileSync(resolve(SRC, "components/ui/dialog.tsx"), "utf8");
const popover = readFileSync(resolve(SRC, "components/ui/popover.tsx"), "utf8");
const tooltip = readFileSync(resolve(SRC, "components/ui/tooltip.tsx"), "utf8");
const select = readFileSync(resolve(SRC, "components/ui/select.tsx"), "utf8");

describe("sticky z-index 栈不变量", () => {
  it("TopBar header 含 z-40（导航层，低于弹窗）", () => {
    expect(topbar).toContain("z-40");
  });
  it("WorkspaceDetail Tabs 容器含 z-30", () => {
    expect(wd).toContain("z-30");
  });
  it("弹窗类（dialog/popover/tooltip/select）统一 z-50，永远最上", () => {
    expect(dialog).toContain("z-50");
    expect(popover).toContain("z-50");
    expect(tooltip).toContain("z-50");
    expect(select).toContain("z-50");
  });
});
