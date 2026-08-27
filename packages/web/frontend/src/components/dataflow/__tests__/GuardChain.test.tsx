import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import i18n from "@/i18n";
import { GuardChain, controlAnchorId } from "../GuardChain";
import type { ControlFinding } from "@/api/types";

const control: ControlFinding = {
  id: "AUTHZ-IDOR-01",
  vuln_class: "authz",
  endpoint: "DELETE /api/users/:id",
  chain: [
    { label: "会话认证", status: "ok", detail: "auth middleware 覆盖该路由", file: "app/mw.ts", line: 10 },
    { label: "owner 校验", status: "missing", detail: "缺少 owner 检查：任意 ID 可删他人资源", file: "app/routes.ts", line: 88 },
    { label: "admin 门禁", status: "ineffective", detail: "角色取自请求体，可伪造", file: "app/guard.ts", line: 5 },
  ],
};

describe("GuardChain — 认证/授权关卡链（spec §5 区 2）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));
  afterEach(() => i18n.changeLanguage("zh"));

  it("空 controls → 不渲染（无认证/授权风险时区隐藏）", () => {
    const { container } = render(<GuardChain controls={[]} />);
    expect(container.querySelector("[data-guard-section]")).toBeNull();
  });

  it("区头：标题「认证 / 授权风险」+ 说明段（关卡分析方法，不画树）", () => {
    const { container } = render(<GuardChain controls={[control]} />);
    const section = container.querySelector("[data-guard-section]")!;
    expect(section).toBeTruthy();
    expect(section.textContent ?? "").toContain("认证 / 授权风险");
    // 说明段解释方法论（白话：不画树、逐接口查关卡）
    expect(section.textContent ?? "").toContain("不画树");
    expect(section.textContent ?? "").toContain("关卡");
  });

  it("逐接口卡：endpoint + 关卡卡序列（三态 status 颜色）", () => {
    const { container } = render(<GuardChain controls={[control]} />);
    const card = container.querySelector('[data-control-id="AUTHZ-IDOR-01"]')!;
    expect(card).toBeTruthy();
    expect(card.textContent ?? "").toContain("DELETE /api/users/:id");

    const ok = container.querySelector('[data-guard-step="ok"]')!;
    expect(ok.className).toContain("guard-ok");
    const missing = container.querySelector('[data-guard-step="missing"]')!;
    expect(missing.className).toContain("guard-missing"); // dashed 红边
    const ineffective = container.querySelector('[data-guard-step="ineffective"]')!;
    expect(ineffective.className).toContain("guard-ineffective");
  });

  it("缺失关卡：dashed 红边 + 流动断线指示（污点穿过的缺口）", () => {
    const { container } = render(<GuardChain controls={[control]} />);
    const missing = container.querySelector('[data-guard-step="missing"]')!;
    expect(missing.className).toContain("guard-missing");
    // 流动断线指示元素
    expect(missing.querySelector("[data-guard-gap]")).toBeTruthy();
    // 白话状态标签
    expect(missing.textContent ?? "").toContain("缺失");
    expect(container.querySelector('[data-guard-step="ok"]')?.textContent ?? "").toContain("正常");
    expect(container.querySelector('[data-guard-step="ineffective"]')?.textContent ?? "").toContain("失效");
  });

  it("detail 引 finding 原文 + file:line", () => {
    const { container } = render(<GuardChain controls={[control]} />);
    const text = container.textContent ?? "";
    expect(text).toContain("auth middleware 覆盖该路由"); // guard 原文
    expect(text).toContain("缺少 owner 检查");
    expect(text).toContain("app/routes.ts:88"); // file:line
    expect(text).toContain("app/mw.ts:10");
  });

  it("controlAnchorId：id 缺失时稳定回退（TOC 与卡片同锚点）", () => {
    expect(controlAnchorId(control, 0)).toBe("AUTHZ-IDOR-01");
    expect(controlAnchorId({ ...control, id: null }, 2)).toBe("ctl-2");
  });

  it("i18n：切英文区头标题", () => {
    i18n.changeLanguage("en");
    const { container } = render(<GuardChain controls={[control]} />);
    expect(container.textContent ?? "").toContain("Authentication / authorization risks");
  });

  it("tokens.css 动效契约：guard-gap-flow 常驻静止，hover/focus-within 才滚动（spec 2026-08-27 §3）", () => {
    const css = readFileSync(resolve(__dirname, "../../../styles/tokens.css"), "utf8");
    // 常驻静止（默认 animation: none——静态红渐隐虚线仍在，滚动是交互反馈）
    expect(css).toMatch(/\.guard-gap-flow\s*\{[^}]*animation:\s*none/);
    // hover / focus-within 触发滚动
    expect(css).toMatch(
      /\.guard-missing:hover \.guard-gap-flow[^{]*,[^{]*\.guard-missing:focus-within \.guard-gap-flow[^{]*\{[^}]*animation:\s*guard-gap-move/,
    );
    // reduced-motion 镜像（触发规则特异性更高，须对齐；聚合所有 reduce 块）
    const reduced = [...css.matchAll(/@media \(prefers-reduced-motion: reduce\)\s*\{([\s\S]*?)\n\}/g)]
      .map((m) => m[1]).join("\n");
    expect(reduced).toMatch(/\.guard-missing:hover \.guard-gap-flow/);
  });
});
