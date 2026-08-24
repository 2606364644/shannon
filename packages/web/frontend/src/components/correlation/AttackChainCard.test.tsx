// D5 组件级测试：AttackChainCard——三段式横排（entry@call_site → method → vuln_refs）、
// confidence 徽标、evidence 折叠（<details>，默认收起点开）。
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import i18n from "@/i18n";
import { AttackChainCard } from "./AttackChainCard";
import type { CorrFlow } from "@/api/types";

const flow: CorrFlow = {
  edge_from: "frontend",
  edge_to: "order-svc",
  entry: "POST /orders",
  method: "order.CreateOrder",
  call_site: { file: "checkout.ts", line: 42, snippet: "await stub.create(order)" },
  vuln_refs: [
    { service: "order-svc", title: "SQL 注入", severity: "high", location: "db.py:10" },
  ],
  confidence: "high",
  evidence: "入口参数未过滤透传到后端拼接 SQL",
};

beforeEach(() => i18n.changeLanguage("zh"));

describe("AttackChainCard", () => {
  it("渲染三段：entry / method / vuln_ref（含 [service] 与 severity·location）", () => {
    render(<AttackChainCard flow={flow} />);
    expect(screen.getByText("POST /orders")).toBeInTheDocument();
    expect(screen.getByText("order.CreateOrder")).toBeInTheDocument();
    expect(screen.getByText("SQL 注入")).toBeInTheDocument();
    expect(screen.getByText("[order-svc]")).toBeInTheDocument();
    expect(screen.getByText(/high · db\.py:10/)).toBeInTheDocument();
  });

  it("第一段带 call_site（@file:line）", () => {
    render(<AttackChainCard flow={flow} />);
    expect(screen.getByText(/@checkout\.ts:42/)).toBeInTheDocument();
  });

  it("confidence 徽标显示置信度值", () => {
    render(<AttackChainCard flow={flow} />);
    expect(screen.getByTestId("chain-confidence").textContent).toBe("high");
  });

  it("evidence 折叠：<details> 默认收起，点 summary 展开", () => {
    render(<AttackChainCard flow={flow} />);
    const details = screen
      .getByText("证据")
      .closest("details") as HTMLDetailsElement | null;
    expect(details).not.toBeNull();
    expect(details!.open).toBe(false);
    // 折叠内容已在 DOM（jsdom 不裁剪），断言存在 + 打开行为
    expect(screen.getByText("入口参数未过滤透传到后端拼接 SQL")).toBeInTheDocument();
    fireEvent.click(screen.getByText("证据"));
    expect(details!.open).toBe(true);
  });
});
