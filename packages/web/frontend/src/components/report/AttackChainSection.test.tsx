import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import i18n from "@/i18n";
import { AttackChainSection } from "./AttackChainSection";

const MD = [
  "### llm-chain-1: XSS -> 劫持",
  "",
  "- **类型:** xss",
  "- **严重程度:** critical",
  "",
  "1. POST /memos 投毒",
  "2. GET /memos 触发",
].join("\n");

beforeEach(() => i18n.changeLanguage("zh"));

describe("AttackChainSection", () => {
  it("渲染章节容器 + 计数徽章", () => {
    render(<AttackChainSection md={MD} count={13} />);
    expect(screen.getByTestId("attack-chain-section")).toBeInTheDocument();
    expect(screen.getByTestId("attack-chain-count").textContent).toContain("13");
  });

  it("渲染标题（攻击链）", () => {
    const { container } = render(<AttackChainSection md={MD} count={1} />);
    expect(container.textContent).toContain("攻击链");
  });

  it("内部渲染 markdown：llm-chain 标题 + 字段 + 步骤", () => {
    const { container } = render(<AttackChainSection md={MD} count={1} />);
    expect(container.textContent).toContain("llm-chain-1");
    expect(container.textContent).toContain("严重程度");
    expect(container.textContent).toContain("POST /memos 投毒");
  });

  it("count=0 正常渲染（章节存在但无条目）", () => {
    render(<AttackChainSection md="仅文字描述" count={0} />);
    expect(screen.getByTestId("attack-chain-section")).toBeInTheDocument();
    expect(screen.getByTestId("attack-chain-count").textContent).toContain("0");
  });

  it("GFM 有序列表渲染成 <ol>", () => {
    const { container } = render(<AttackChainSection md={MD} count={1} />);
    expect(container.querySelector("ol")).not.toBeNull();
  });
});
