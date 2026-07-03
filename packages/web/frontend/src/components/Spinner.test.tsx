import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Spinner } from "./Spinner";

describe("Spinner", () => {
  it("渲染 label + role=status（a11y）", () => {
    render(<Spinner label="loading" />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByText(/loading/)).toBeInTheDocument();
  });
  it("无 label 也渲染（aria-live polite）", () => {
    const { container } = render(<Spinner />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(container.querySelector(".shannon-spinner")).toBeTruthy();
  });
});
