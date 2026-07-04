import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { VulnCard, MergeSourceBadge } from "./VulnCard";
import type { Vulnerability } from "../api/types";

const base: Vulnerability = {
  ID: "SSRF-01",
  vulnerability_type: "URL_Manipulation",
  externally_exploitable: false,
};

describe("MergeSourceBadge", () => {
  it("llm-only → 💭 LLM轨", () => {
    render(<MergeSourceBadge src="llm-only" />);
    expect(screen.getByText(/LLM轨/)).toBeInTheDocument();
  });
  it("gitnexus-only → 🔍 GN轨", () => {
    render(<MergeSourceBadge src="gitnexus-only" />);
    expect(screen.getByText(/GN轨/)).toBeInTheDocument();
  });
  it("both → ✓ 双轨确认", () => {
    render(<MergeSourceBadge src="both" />);
    expect(screen.getByText(/双轨确认/)).toBeInTheDocument();
  });
  it("未知字符串 → outline badge 透传原值（literal-guard）", () => {
    render(<MergeSourceBadge src="unexpected" />);
    expect(screen.getByText("unexpected")).toBeInTheDocument();
    expect(screen.queryByText(/LLM轨/)).not.toBeInTheDocument();
  });
  it("undefined → null（不渲染）", () => {
    const { container } = render(<MergeSourceBadge src={undefined} />);
    // Badge 渲染为 div；断言无 Badge 子节点
    expect(container.querySelector("[class*='border']")).toBeNull();
  });
});

describe("VulnCard", () => {
  it("header 显示 ID + vulnerability_type", () => {
    render(<VulnCard v={base} />);
    expect(screen.getByText("SSRF-01")).toBeInTheDocument();
    expect(screen.getByText(/URL_Manipulation/)).toBeInTheDocument();
  });

  it("externally_exploitable=true → 可达 ● 徽章 + red 边框语义", () => {
    const { container } = render(<VulnCard v={{ ...base, externally_exploitable: true }} />);
    expect(screen.getByText(/可达/)).toBeInTheDocument();
    // red 边框语义：Card 根节点 border-red/50
    expect(container.firstChild).toHaveClass("border-red/50");
  });

  it("externally_exploitable=false → 无可达徽章 + 无 red 边框", () => {
    const { container } = render(<VulnCard v={base} />);
    expect(screen.queryByText(/可达/)).not.toBeInTheDocument();
    expect(container.firstChild).not.toHaveClass("border-red/50");
  });

  it("点击 head 展开/收起 detail（结构性：detail 字段渲染依赖 open）", () => {
    const v: Vulnerability = {
      ...base,
      vulnerable_code_location: "src/foo.ts:42",
      missing_defense: "缺少输入校验",
      exploitation_hypothesis: "可篡改 url",
      suggested_exploit_technique: "curl 攻击",
      notes: "重要",
    };
    render(<VulnCard v={v} />);
    // 初始收起
    expect(screen.queryByText(/缺少输入校验/)).not.toBeInTheDocument();
    // 展开（点 header role=button）
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText(/缺少输入校验/)).toBeInTheDocument();
    expect(screen.getByText("src/foo.ts:42")).toBeInTheDocument();
    expect(screen.getByText(/可篡改 url/)).toBeInTheDocument();
    // 收起
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByText(/缺少输入校验/)).not.toBeInTheDocument();
  });

  it("vc-head 有 role=button + aria-expanded，回车切换展开", () => {
    render(<VulnCard v={{ ID: "INJ-01", vulnerability_type: "sqli", externally_exploitable: false }} />);
    const head = screen.getByRole("button");
    expect(head).toHaveAttribute("aria-expanded", "false");
    fireEvent.keyDown(head, { key: "Enter" });
    expect(head).toHaveAttribute("aria-expanded", "true");
  });

  it("可达漏洞有 red 边框语义", () => {
    render(<VulnCard v={{ ID: "X", vulnerability_type: "t", externally_exploitable: true }} />);
    // 可达徽章
    expect(screen.getByText(/可达/)).toBeInTheDocument();
  });
});
