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
});
