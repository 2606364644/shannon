import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MarkdownVulnCard } from "./MarkdownVulnCard";
import type { ParsedVulnBlock } from "../api/types";

function makeBlock(overrides: Partial<ParsedVulnBlock> = {}): ParsedVulnBlock {
  return {
    id: "XSS-VULN-04",
    prefix: "XSS",
    title: "测试漏洞",
    starred: false,
    vulnType: "Stored",
    fields: [],
    externallyExploitable: null,
    authRequired: null,
    confidence: null,
    verdict: null,
    raw: "",
    ...overrides,
  };
}

describe("MarkdownVulnCard · severity 着色", () => {
  it("Critical → 红边框 + data-severity", () => {
    const { container } = render(<MarkdownVulnCard block={makeBlock()} severity="Critical" />);
    const card = container.querySelector('[data-testid="vuln-card"]');
    expect(card).toHaveAttribute("data-severity", "Critical");
    expect(card?.className).toMatch(/border-red/);
    // 左色条 bg-red
    const stripe = container.querySelector(".bg-red");
    expect(stripe).not.toBeNull();
  });

  it("High → 橙边框 + bg-orange 色条", () => {
    const { container } = render(<MarkdownVulnCard block={makeBlock()} severity="High" />);
    const card = container.querySelector('[data-testid="vuln-card"]');
    expect(card).toHaveAttribute("data-severity", "High");
    expect(card?.className).toMatch(/border-orange/);
    expect(container.querySelector(".bg-orange")).not.toBeNull();
  });

  it("Medium → 金边框 + bg-yellow 色条", () => {
    const { container } = render(<MarkdownVulnCard block={makeBlock()} severity="Medium" />);
    expect(container.querySelector('[data-testid="vuln-card"]')?.className).toMatch(/border-yellow/);
    expect(container.querySelector(".bg-yellow")).not.toBeNull();
  });

  it("Low → 灰边框 + bg-muted-foreground 色条", () => {
    const { container } = render(<MarkdownVulnCard block={makeBlock()} severity="Low" />);
    const card = container.querySelector('[data-testid="vuln-card"]');
    expect(card).toHaveAttribute("data-severity", "Low");
    expect(card?.className).toMatch(/border-border(?!\/)/);
    expect(container.querySelector(".bg-muted-foreground")).not.toBeNull();
  });

  it("severity 角标显示等级 + 推断", () => {
    render(<MarkdownVulnCard block={makeBlock()} severity="Critical" />);
    const badge = screen.getByTestId("severity-badge");
    expect(badge).toHaveTextContent("Critical");
    expect(badge).toHaveTextContent("推断");
  });
});

describe("MarkdownVulnCard · 头部信号", () => {
  it("显示 vuln id + 类型", () => {
    render(<MarkdownVulnCard block={makeBlock()} severity="High" />);
    expect(screen.getByTestId("vuln-id")).toHaveTextContent("XSS-VULN-04");
    expect(screen.getByText("Stored")).toBeInTheDocument();
  });

  it("starred → ★ 首要 徽章", () => {
    render(<MarkdownVulnCard block={makeBlock({ starred: true })} severity="Critical" />);
    expect(screen.getByText(/首要/)).toBeInTheDocument();
  });

  it("externallyExploitable=true → 🌐 公网", () => {
    render(<MarkdownVulnCard block={makeBlock({ externallyExploitable: true })} severity="High" />);
    expect(screen.getByText(/公网/)).toBeInTheDocument();
  });

  it("authRequired=false → pre-auth 标记", () => {
    render(<MarkdownVulnCard block={makeBlock({ authRequired: false })} severity="High" />);
    expect(screen.getByText(/pre-auth/)).toBeInTheDocument();
  });

  it("authRequired=true → auth 标记", () => {
    render(<MarkdownVulnCard block={makeBlock({ authRequired: true })} severity="Medium" />);
    expect(screen.getByText(/auth/)).toBeInTheDocument();
  });

  it("confidence 显示", () => {
    render(<MarkdownVulnCard block={makeBlock({ confidence: "high" })} severity="High" />);
    expect(screen.getByText(/high/)).toBeInTheDocument();
  });
});

describe("MarkdownVulnCard · kv-list", () => {
  it("fields 渲染为 li[data-testid=kv-row]", () => {
    const block = makeBlock({
      fields: [
        { key: "source", val: "`POST /x` body" },
        { key: "sink", val: "eval()" },
      ],
    });
    const { container } = render(<MarkdownVulnCard block={block} severity="High" />);
    expect(container.querySelectorAll('[data-testid="kv-row"]').length).toBe(2);
    expect(screen.getByText("source")).toBeInTheDocument();
    expect(screen.getByText("sink")).toBeInTheDocument();
  });

  it("kv-val inline code 解析（反引号段渲染为 code）", () => {
    const block = makeBlock({ fields: [{ key: "source", val: "`POST /signup` body" }] });
    render(<MarkdownVulnCard block={block} severity="High" />);
    expect(screen.getByText("POST /signup")).toBeInTheDocument();
  });
});

describe("MarkdownVulnCard · PoC 折叠（a11y）", () => {
  it("初始收起：aria-expanded=false + 无 poc-code", () => {
    render(<MarkdownVulnCard block={makeBlock({ witnessPayload: "alert(1)" })} severity="High" />);
    const toggle = screen.getByTestId("poc-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAttribute("role", "button");
    expect(screen.queryByTestId("poc-code")).not.toBeInTheDocument();
  });

  it("点击 toggle 展开：aria-expanded=true + poc-code 显示", () => {
    render(<MarkdownVulnCard block={makeBlock({ witnessPayload: "alert(1)" })} severity="High" />);
    const toggle = screen.getByTestId("poc-toggle");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("poc-code")).toHaveTextContent("alert(1)");
  });

  it("再次点击收起", () => {
    render(<MarkdownVulnCard block={makeBlock({ witnessPayload: "alert(1)" })} severity="High" />);
    const toggle = screen.getByTestId("poc-toggle");
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("poc-code")).not.toBeInTheDocument();
  });

  it("Enter 键展开", () => {
    render(<MarkdownVulnCard block={makeBlock({ witnessPayload: "alert(1)" })} severity="High" />);
    const toggle = screen.getByTestId("poc-toggle");
    fireEvent.keyDown(toggle, { key: "Enter" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });

  it("Space 键展开（阻止默认滚动）", () => {
    render(<MarkdownVulnCard block={makeBlock({ witnessPayload: "alert(1)" })} severity="High" />);
    const toggle = screen.getByTestId("poc-toggle");
    fireEvent.keyDown(toggle, { key: " " });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });

  it("无 witnessPayload → 不渲染 PoC toggle", () => {
    render(<MarkdownVulnCard block={makeBlock()} severity="Low" />);
    expect(screen.queryByTestId("poc-toggle")).not.toBeInTheDocument();
  });
});
