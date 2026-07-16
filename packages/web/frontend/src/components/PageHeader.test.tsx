import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { PageHeader } from "./PageHeader";

describe("PageHeader", () => {
  it("把 title 渲染为 h1", () => {
    render(<PageHeader title="工作区" />);
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1).toHaveTextContent("工作区");
  });

  it("提供 subtitle 时渲染副标题文案", () => {
    render(<PageHeader title="工作区" subtitle="所有扫描任务与产物" />);
    expect(screen.getByText("所有扫描任务与产物")).toBeInTheDocument();
  });

  it("副标题用 muted 色（text-muted-foreground）弱化层级", () => {
    const { container } = render(<PageHeader title="仓库" subtitle="已纳管" />);
    const sub = container.querySelector(".text-muted-foreground");
    expect(sub).not.toBeNull();
    expect(sub).toHaveTextContent("已纳管");
  });

  it("不提供 subtitle 时只渲染 h1、无副标题节点", () => {
    const { container } = render(<PageHeader title="工作区" />);
    expect(container.querySelectorAll("h1, h2, h3, p")).toHaveLength(1);
  });
});
