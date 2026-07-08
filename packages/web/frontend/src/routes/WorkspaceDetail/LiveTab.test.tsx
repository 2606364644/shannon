import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import LiveTab from "./LiveTab";

// mock useEventSource 返回受控 events（module-level mutable，每 test 改写）
const eventsState: { events: any[]; status: string } = { events: [], status: "open" };
vi.mock("../../api/useEventSource", () => ({
  useEventSource: () => eventsState,
}));

function renderLive() {
  return render(
    <MemoryRouter initialEntries={["/p/ws/live"]}>
      <Routes><Route path="/p/:workspace/live" element={<LiveTab />} /></Routes>
    </MemoryRouter>,
  );
}

describe("LiveTab", () => {
  it("渲染 DashboardPanel + LogStream 容器（aria-live）", () => {
    eventsState.events = [];
    eventsState.status = "open";
    renderLive();
    // LogStream 提供 aria-live=polite
    expect(document.querySelector('[aria-live="polite"]')).toBeInTheDocument();
  });

  it("连接态徽章显示 已连接（status=open）", () => {
    eventsState.events = [];
    eventsState.status = "open";
    renderLive();
    expect(screen.getByText("已连接")).toBeInTheDocument();
  });

  it("连接态徽章显示 重连中（status=error）", () => {
    eventsState.events = [];
    eventsState.status = "error";
    renderLive();
    expect(screen.getByText("重连中")).toBeInTheDocument();
  });

  it("scan_end 后显示查看报告按钮", () => {
    eventsState.events = [
      { type: "scan_end", status: "completed", ts: "2026-01-01T00:00:00Z", category: "CONTROL" },
    ];
    eventsState.status = "closed";
    renderLive();
    expect(screen.getByRole("button", { name: /查看报告/ })).toBeInTheDocument();
  });

  it("scan_end=interrupted 显失败原因、不显查看报告", () => {
    eventsState.events = [
      { type: "scan_end", status: "interrupted", stderr_tail: "扫描因服务重启被中断", ts: "2026-01-01T00:00:00Z", category: "CONTROL" },
    ];
    eventsState.status = "closed";
    renderLive();
    expect(screen.getByText(/扫描已中断/)).toBeInTheDocument();
    expect(screen.getByText(/扫描因服务重启被中断/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /查看报告/ })).not.toBeInTheDocument();
  });

  it("error + 空 events 显重连/无进度提示", () => {
    eventsState.events = [];
    eventsState.status = "error";
    renderLive();
    expect(screen.getByText(/正在重连实时通道|暂无进度数据/)).toBeInTheDocument();
  });

  it("elapsed 从 PhaseEvent(start) ts 推导（≥ 5s，显示 MM:SS）", async () => {
    const pastTs = new Date(Date.now() - 5000).toISOString();
    eventsState.events = [
      {
        type: "PhaseEvent", event: "start", phase: "recon",
        steps: [], step_intents: [], ts: pastTs, category: "PHASE",
      },
    ];
    eventsState.status = "open";
    renderLive();
    // 初始 tick 即 5000ms → "00:05"
    await waitFor(() => {
      expect(screen.getByText(/00:0[0-9]/)).toBeInTheDocument();
    });
  });

  it("reducer 增量 fold：PhaseEvent 后 current_phase 出现", async () => {
    eventsState.events = [
      {
        type: "PhaseEvent", event: "start", phase: "vulnerability-analysis",
        steps: ["injection", "xss"], step_intents: ["i", "x"],
        ts: new Date().toISOString(), category: "PHASE",
      },
    ];
    eventsState.status = "open";
    renderLive();
    // DashboardPanel 渲染 current_phase（font-bold text-cyan 类名是 DashboardPanel phase 标签）
    await waitFor(() => {
      const matches = screen.getAllByText(/vulnerability-analysis/);
      expect(matches.some((el) => el.classList.contains("font-bold"))).toBe(true);
    });
  });
});
