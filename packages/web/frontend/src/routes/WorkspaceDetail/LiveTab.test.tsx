import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import i18n from "@/i18n";
import LiveTab from "./LiveTab";

// mock useEventSource 返回受控 events + status（module-level mutable，每 test 改写）
const eventsState: { events: any[]; status: string } = { events: [], status: "open" };
vi.mock("../../api/useEventSource", () => ({
  useEventSource: () => eventsState,
}));

// scanEventsUrl 透传真实（仅拼 URL 字符串，无 IO）；LiveTab 瘦身后不再调 getScan。
vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return { ...actual };
});

function renderLive() {
  return render(
    <MemoryRouter initialEntries={["/p/ws/scans/scan1/live"]}>
      <Routes><Route path="/p/:workspace/scans/:scanId/live" element={<LiveTab />} /></Routes>
    </MemoryRouter>,
  );
}

// jsdom navigator.language 默认 en；现有断言依赖中文渲染，逐测试钉回 zh。
beforeEach(() => { i18n.changeLanguage("zh"); eventsState.events = []; eventsState.status = "open"; });

describe("LiveTab", () => {
  it("渲染 LogStream 容器（aria-live）", () => {
    renderLive();
    expect(document.querySelector('[aria-live="polite"]')).toBeInTheDocument();
  });

  it("连接态徽章显示 已连接（status=open）", () => {
    eventsState.status = "open";
    renderLive();
    expect(screen.getByText("已连接")).toBeInTheDocument();
  });

  it("连接态徽章显示 重连中（status=error）", () => {
    eventsState.status = "error";
    renderLive();
    expect(screen.getByText("重连中")).toBeInTheDocument();
  });

  it("scan_end completed 后显示查看报告按钮", () => {
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
});

describe("LiveTab i18n", () => {
  afterEach(() => i18n.changeLanguage("zh"));

  it("切英文后连接态徽章变 Connected", async () => {
    eventsState.events = [];
    eventsState.status = "open";
    renderLive();
    expect(screen.getByText("已连接")).toBeInTheDocument();
    await i18n.changeLanguage("en");
    expect(await screen.findByText("Connected")).toBeInTheDocument();
  });

  it("切英文后 scan_end completed 显示 View report 按钮", async () => {
    eventsState.events = [
      { type: "scan_end", status: "completed", ts: "2026-01-01T00:00:00Z", category: "CONTROL" },
    ];
    eventsState.status = "closed";
    renderLive();
    expect(screen.getByRole("button", { name: /查看报告/ })).toBeInTheDocument();
    await i18n.changeLanguage("en");
    expect(await screen.findByRole("button", { name: /View report/ })).toBeInTheDocument();
  });

  it("切英文后 scan_end interrupted 显示 Scan interrupted", async () => {
    eventsState.events = [
      { type: "scan_end", status: "interrupted", stderr_tail: "扫描因服务重启被中断", ts: "2026-01-01T00:00:00Z", category: "CONTROL" },
    ];
    eventsState.status = "closed";
    renderLive();
    expect(screen.getByText(/扫描已中断/)).toBeInTheDocument();
    await i18n.changeLanguage("en");
    expect(await screen.findByText(/Scan interrupted/)).toBeInTheDocument();
    // stderr_tail 是事件流数据，不随语言变化
    expect(screen.getByText(/扫描因服务重启被中断/)).toBeInTheDocument();
  });
});
