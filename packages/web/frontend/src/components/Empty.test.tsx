import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Empty } from "./Empty";

describe("Empty", () => {
  it("渲染 icon + title + hint", () => {
    render(<Empty icon="∅" title="no workspaces" hint="新建一个扫描" />);
    expect(screen.getByText("no workspaces")).toBeInTheDocument();
    expect(screen.getByText("新建一个扫描")).toBeInTheDocument();
    expect(screen.getByText("∅")).toBeInTheDocument();
  });
  it("无 icon 用默认 ∅", () => {
    render(<Empty title="empty" />);
    expect(screen.getByText("∅")).toBeInTheDocument();
  });
  it("渲染 children CTA slot", () => {
    render(<Empty title="empty"><button>CTA</button></Empty>);
    expect(screen.getByRole("button", { name: "CTA" })).toBeInTheDocument();
  });
});
