import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
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
  it("reachable=true → ● 可达 + red", () => {
    const { container } = render(<ReachableBadge reachable={true} />);
    expect(container.textContent).toMatch(/可达/);
    expect(container.querySelector(".text-red")).toBeTruthy();
  });
  it("reachable=false → ○ 内部 + muted", () => {
    render(<ReachableBadge reachable={false} />);
    expect(screen.getByText(/内部/)).toBeInTheDocument();
  });
});

describe("card-reachable utility（spec §4 Card 可达性变体）", () => {
  it("index.css 含 .card-reachable 规则消费 --red", () => {
    const css = readFileSync(resolve(__dirname, "../styles/index.css"), "utf8");
    expect(css).toContain(".card-reachable");
    expect(css).toContain("hsl(var(--c-red))");
  });
});
