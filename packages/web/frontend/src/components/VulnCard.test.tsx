import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import i18n from "@/i18n";
import { VulnCard, MergeSourceBadge } from "./VulnCard";
import type { Vulnerability } from "../api/types";

const base: Vulnerability = {
  ID: "SSRF-01",
  vulnerability_type: "URL_Manipulation",
  externally_exploitable: false,
};

describe("MergeSourceBadge", () => {
  beforeEach(() => i18n.changeLanguage("zh"));
  afterEach(() => i18n.changeLanguage("zh"));

  it("llm-only → 💭 LLM 轨", () => {
    render(<MergeSourceBadge src="llm-only" />);
    expect(screen.getByText(/LLM 轨/)).toBeInTheDocument();
  });
  it("gitnexus-only → 🔍 GN 轨", () => {
    render(<MergeSourceBadge src="gitnexus-only" />);
    expect(screen.getByText(/GN 轨/)).toBeInTheDocument();
  });
  it("both → ✓ 双轨确认", () => {
    render(<MergeSourceBadge src="both" />);
    expect(screen.getByText(/双轨确认/)).toBeInTheDocument();
  });
  it("未知字符串 → outline badge 透传原值（literal-guard）", () => {
    render(<MergeSourceBadge src="unexpected" />);
    expect(screen.getByText("unexpected")).toBeInTheDocument();
    expect(screen.queryByText(/LLM 轨/)).not.toBeInTheDocument();
  });
  it("undefined → null（不渲染）", () => {
    const { container } = render(<MergeSourceBadge src={undefined} />);
    // Badge 渲染为 div；断言无 Badge 子节点
    expect(container.querySelector("[class*='border']")).toBeNull();
  });

  it("i18n: 切英文徽章文案", () => {
    i18n.changeLanguage("en");
    const { rerender } = render(<MergeSourceBadge src="llm-only" />);
    expect(screen.getByText(/LLM track/)).toBeInTheDocument();
    rerender(<MergeSourceBadge src="gitnexus-only" />);
    expect(screen.getByText(/GN track/)).toBeInTheDocument();
    rerender(<MergeSourceBadge src="both" />);
    expect(screen.getByText(/Dual-track confirmed/)).toBeInTheDocument();
  });
});

describe("VulnCard", () => {
  beforeEach(() => i18n.changeLanguage("zh"));
  afterEach(() => i18n.changeLanguage("zh"));

  it("header 显示 ID + vulnerability_type", () => {
    render(<VulnCard v={base} />);
    expect(screen.getByText("SSRF-01")).toBeInTheDocument();
    expect(screen.getByText(/URL_Manipulation/)).toBeInTheDocument();
  });

  it("title 存在时展示 title 而非 vulnerability_type（spec 2026-08-06）", () => {
    render(<VulnCard v={{ ...base, title: "SSRF via webhook_url in /api/webhooks" }} />);
    expect(screen.getByText(/SSRF via webhook_url/)).toBeInTheDocument();
    // title 存在时不再展示裸 vulnerability_type
    expect(screen.queryByText(/URL_Manipulation/)).not.toBeInTheDocument();
  });

  it("title 为空时退化到 vulnerability_type", () => {
    render(<VulnCard v={{ ...base, title: undefined }} />);
    expect(screen.getByText(/URL_Manipulation/)).toBeInTheDocument();
  });

  it("ID 是标识符不是警报：中性 semibold，不再 text-red（spec 2026-08-27 §2.1）", () => {
    render(<VulnCard v={base} />);
    const id = screen.getByText("SSRF-01");
    expect(id.className).toContain("text-foreground");
    expect(id.className).toContain("font-semibold");
    expect(id.className).not.toContain("text-red");
  });

  it("externally_exploitable=true → 可达徽章中性 ⌖ 字形（非红，不与 severity 抢通道）", () => {
    const { container } = render(<VulnCard v={{ ...base, externally_exploitable: true }} />);
    const badge = screen.getByText(/⌖/);
    expect(badge.textContent).toContain("可达");
    // 红色稀缺预算：可达徽章不走红通道
    expect(badge.className).not.toContain("text-red");
    expect(badge.className).not.toContain("border-red");
    // 可达性不再整卡红边（与 severity 双重编码冲突）
    expect(container.firstChild).not.toHaveClass("border-red/50");
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

  it("可达漏洞有中性可达徽章（red 边框语义已撤，spec 2026-08-27 §2.1）", () => {
    const { container } = render(<VulnCard v={{ ID: "X", vulnerability_type: "t", externally_exploitable: true }} />);
    expect(screen.getByText(/可达/)).toBeInTheDocument();
    expect(container.firstChild).not.toHaveClass("border-red/50");
  });

  it("i18n: 切英文可达徽章为 Reachable", () => {
    i18n.changeLanguage("en");
    render(<VulnCard v={{ ID: "X", vulnerability_type: "t", externally_exploitable: true }} />);
    expect(screen.getByText(/Reachable/)).toBeInTheDocument();
  });
});
