// D5 组件级测试：TopologyGraph——layout 纯函数（入口列左 / 后端网格列右）+
// 节点/边渲染（role 徽标 / protocol 中点标签 / declared-missing 虚线）+
// 点边展开 calls 表（method / file:line / snippet / confidence / evidence）。
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import i18n from "@/i18n";
import { TopologyGraph, layout } from "./TopologyGraph";
import type { CorrelationDetail } from "@/api/types";

type Topology = NonNullable<CorrelationDetail["topology"]>;

const topology: Topology = {
  services: [
    { name: "frontend", role: "entrypoint", repo: "frontend" },
    { name: "order-svc", role: "backend", repo: "order-svc" },
    { name: "admin", role: "backend", repo: "admin" },
  ],
  edges: [
    {
      from: "frontend",
      to: "order-svc",
      protocol: "grpc",
      status: "ok",
      calls: [
        {
          method: "order.CreateOrder",
          call_site: { file: "checkout.ts", line: 42, snippet: "await stub.create(order)" },
          confidence: "high",
          evidence: "grpc client stub 直连 order-svc",
        },
      ],
    },
    {
      from: "admin",
      to: "order-svc",
      protocol: "http",
      status: "declared-missing",
      calls: [],
    },
  ],
};

beforeEach(() => i18n.changeLanguage("zh"));

describe("layout 纯函数", () => {
  it("entrypoint 落左列（x=90）、backend 落右列（x=430）", () => {
    const { nodes } = layout(topology.services);
    const frontend = nodes.find((n) => n.name === "frontend")!;
    const order = nodes.find((n) => n.name === "order-svc")!;
    const admin = nodes.find((n) => n.name === "admin")!;
    expect(frontend.x).toBe(90);
    expect(frontend.role).toBe("entrypoint");
    expect(order.x).toBe(430);
    expect(admin.x).toBe(430);
    // 右列垂直均分：order 在 admin 上方
    expect(order.y).toBeLessThan(admin.y);
  });

  it("height = max(左右列节点数, 1) × heightPerNode + 40；空服务不塌缩", () => {
    expect(layout([]).height).toBe(1 * 90 + 40);
    expect(layout(topology.services).height).toBe(Math.max(1, 2) * 90 + 40);
    // 只有入口（1 入口 0 后端）：取 max(1,1)
    expect(layout([{ name: "fe", role: "entrypoint" }]).height).toBe(1 * 90 + 40);
  });
});

describe("TopologyGraph", () => {
  it("渲染节点名 + role 徽标（入口 / 后端）", () => {
    render(<TopologyGraph topology={topology} />);
    expect(screen.getByText("frontend")).toBeInTheDocument();
    expect(screen.getByText("order-svc")).toBeInTheDocument();
    expect(screen.getByText("入口")).toBeInTheDocument();
    // 两个 backend 节点（order-svc / admin）各带一枚「后端」徽标
    expect(screen.getAllByText("后端").length).toBe(2);
  });

  it("渲染边 protocol 中点标签（grpc / http）", () => {
    render(<TopologyGraph topology={topology} />);
    expect(screen.getByText("grpc")).toBeInTheDocument();
    expect(screen.getByText("http")).toBeInTheDocument();
  });

  it("declared-missing 边渲染虚线（stroke-dasharray），ok 边实线", () => {
    render(<TopologyGraph topology={topology} />);
    // g 里第一条 path 是透明命中区（无样式），可见线 = .stroke-current
    const dashed = document.querySelector(
      '[data-testid="topo-edge-admin-order-svc"] path.stroke-current');
    const solid = document.querySelector(
      '[data-testid="topo-edge-frontend-order-svc"] path.stroke-current');
    expect(dashed?.getAttribute("stroke-dasharray")).toBe("6 4");
    expect(solid?.getAttribute("stroke-dasharray")).toBeNull();
  });

  it("点边 → 下方展开该边 calls 表（method / file:line / snippet / confidence / evidence）", () => {
    render(<TopologyGraph topology={topology} />);
    expect(screen.queryByTestId("topo-calls")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("topo-edge-frontend-order-svc"));
    const calls = screen.getByTestId("topo-calls");
    expect(calls).toBeInTheDocument();
    expect(screen.getByText("order.CreateOrder")).toBeInTheDocument();
    expect(screen.getByText("checkout.ts:42")).toBeInTheDocument();
    expect(screen.getByText("await stub.create(order)")).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText("grpc client stub 直连 order-svc")).toBeInTheDocument();
  });

  it("点无 calls 的边 → 展开区显示「无调用记录」空提示", () => {
    render(<TopologyGraph topology={topology} />);
    fireEvent.click(screen.getByTestId("topo-edge-admin-order-svc"));
    expect(screen.getByTestId("topo-calls")).toBeInTheDocument();
    expect(screen.getByText("无调用记录")).toBeInTheDocument();
  });

  it("再点同一条边收起 calls 表", () => {
    render(<TopologyGraph topology={topology} />);
    const edge = screen.getByTestId("topo-edge-frontend-order-svc");
    fireEvent.click(edge);
    expect(screen.getByTestId("topo-calls")).toBeInTheDocument();
    fireEvent.click(edge);
    expect(screen.queryByTestId("topo-calls")).not.toBeInTheDocument();
  });
});
