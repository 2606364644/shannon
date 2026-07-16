import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatRow } from "./StatRow";

describe("StatRow", () => {
  it("渲染每个 stat 的 label 与 value", () => {
    render(
      <StatRow
        stats={[
          { label: "运行中", value: 2 },
          { label: "已完成", value: 14 },
        ]}
      />
    );
    expect(screen.getByText("运行中")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument();
  });

  it("tone=cyan 的 value 带 text-cyan 着色", () => {
    const { container } = render(
      <StatRow stats={[{ label: "运行中", value: 2, tone: "cyan" }]} />
    );
    const val = container.querySelector(".tabular-nums.text-cyan");
    expect(val).not.toBeNull();
    expect(val).toHaveTextContent("2");
  });

  it("tone=green / red 分别着色对应 value", () => {
    const { container } = render(
      <StatRow
        stats={[
          { label: "已完成", value: 14, tone: "green" },
          { label: "失败", value: 3, tone: "red" },
        ]}
      />
    );
    expect(container.querySelector(".text-green")?.textContent).toBe("14");
    expect(container.querySelector(".text-red")?.textContent).toBe("3");
  });

  it("默认 tone 不带任何语义色 class", () => {
    const { container } = render(<StatRow stats={[{ label: "仓库", value: 12 }]} />);
    expect(container.querySelector(".text-cyan, .text-green, .text-red")).toBeNull();
  });

  it("label 用 uppercase 弱化为辅助层级", () => {
    const { container } = render(<StatRow stats={[{ label: "总成本", value: "¥86" }]} />);
    expect(container.querySelector(".uppercase")).toHaveTextContent("总成本");
  });
});
