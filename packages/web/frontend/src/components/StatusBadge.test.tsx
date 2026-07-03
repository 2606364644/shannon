import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it("running → ● + 文案", () => {
    render(<StatusBadge status="running" />);
    expect(screen.getByText(/running/)).toBeInTheDocument();
    expect(screen.getByText(/running/).closest(".status-badge")?.querySelector(".mono")?.textContent).toBe("●");
  });
  it("completed → ✓", () => {
    const { container } = render(<StatusBadge status="completed" />);
    expect(container.querySelector(".mono")?.textContent).toBe("✓");
  });
  it("correlation → 🔗", () => {
    const { container } = render(<StatusBadge status="running" correlation />);
    expect(container.textContent).toContain("🔗");
  });
  it("a11y：title 属性 = status 字符串（符号 ●✓✗⚠ 不应是唯一信号）", () => {
    const { container } = render(<StatusBadge status="running" />);
    const badge = container.querySelector(".status-badge");
    expect(badge?.getAttribute("title")).toBe("running");
  });
  it("a11y：未知 status 也有 title", () => {
    const { container } = render(<StatusBadge status="weird-state" />);
    const badge = container.querySelector(".status-badge");
    expect(badge?.getAttribute("title")).toBe("weird-state");
  });
});
