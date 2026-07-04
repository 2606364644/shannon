import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it("running → ● + 文案（Badge 渲染）", () => {
    const { container } = render(<StatusBadge status="running" />);
    expect(screen.getByText(/running/)).toBeInTheDocument();
    // shadcn Badge 渲染为外层 <div>（含 text-* 语义色），内含 <span aria-hidden> 承载图标
    const badge = container.querySelector("[class*='text-cyan']");
    expect(badge).not.toBeNull();
    expect(badge?.querySelector("[aria-hidden]")?.textContent).toBe("●");
  });
  it("completed 渲染 Badge + green 语义色", () => {
    render(<StatusBadge status="completed" />);
    // 拒绝 weak `??` 兜底：直接断言承载 completed 文案的 Badge 带 text-green
    const node = screen.getByText("completed").closest("[class*='text-green']");
    expect(node).not.toBeNull();
    expect(node).toBeInTheDocument();
  });
  it("correlation → 🔗", () => {
    const { container } = render(<StatusBadge status="running" correlation />);
    expect(container.textContent).toContain("🔗");
  });
  it("a11y：title 属性 = status 字符串（符号 ●✓✗⚠ 不应是唯一信号）", () => {
    const { container } = render(<StatusBadge status="running" />);
    const badge = container.querySelector("[title='running']");
    expect(badge?.getAttribute("title")).toBe("running");
  });
  it("a11y：未知 status 也有 title", () => {
    const { container } = render(<StatusBadge status="weird-state" />);
    const badge = container.querySelector("[title='weird-state']");
    expect(badge?.getAttribute("title")).toBe("weird-state");
  });
  it("未知 status 走 warn 色 + ? 图标", () => {
    const { container } = render(<StatusBadge status="weird" />);
    expect(screen.getByText(/weird/)).toBeInTheDocument();
    const badge = container.querySelector("[class*='text-yellow']");
    expect(badge?.className).toMatch(/text-yellow/);
    expect(badge?.querySelector("[aria-hidden]")?.textContent).toBe("?");
  });
});
