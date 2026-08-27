import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MergeSourceBadge, ReachableBadge } from "./vuln-badges";

describe("MergeSourceBadge", () => {
  it("llm-only → 💭 LLM轨 + magenta", () => {
    const { container } = render(<MergeSourceBadge source="llm-only" />);
    expect(screen.getByText(/LLM轨/)).toBeInTheDocument();
    expect(container.querySelector(".text-magenta")).toBeTruthy();
  });
  it("gitnexus-only → 🔍 GN轨 + cyan", () => {
    const { container } = render(<MergeSourceBadge source="gitnexus-only" />);
    expect(screen.getByText(/GN轨/)).toBeInTheDocument();
    expect(container.querySelector(".text-cyan")).toBeTruthy();
  });
  it("both → ✓ 双轨确认 + green", () => {
    const { container } = render(<MergeSourceBadge source="both" />);
    expect(screen.getByText(/双轨确认/)).toBeInTheDocument();
    expect(container.querySelector(".text-green")).toBeTruthy();
  });
});

describe("ReachableBadge", () => {
  it("reachable=true → ⌖ 可达 中性（不与 severity 抢红色通道，spec 2026-08-27 §2.1）", () => {
    const { container } = render(<ReachableBadge reachable={true} />);
    expect(container.textContent).toMatch(/⌖ 可达/);
    expect(container.querySelector(".text-red")).toBeNull();
  });
  it("reachable=false → ○ 内部 + muted", () => {
    render(<ReachableBadge reachable={false} />);
    expect(screen.getByText(/内部/)).toBeInTheDocument();
  });
});
