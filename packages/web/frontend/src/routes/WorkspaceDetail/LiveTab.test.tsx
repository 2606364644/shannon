import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import i18n from "@/i18n";
import LiveTab from "./LiveTab";

// mock useEventSource 返回受控 events（module-level mutable，每 test 改写）
const eventsState: { events: any[]; status: string } = { events: [], status: "open" };
vi.mock("../../api/useEventSource", () => ({
  useEventSource: () => eventsState,
}));

// mock getScan 返回受控 SessionData（开始时间 / 总耗时 / cost 兜底数据源）。
// scanEventsUrl 透传真实（仅拼 URL 字符串，无 IO）。
const scanMetaState: { meta: any } = { meta: null };
vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return {
    ...actual,
    getScan: () => Promise.resolve(scanMetaState.meta),
  };
});

function renderLive() {
  return render(
    <MemoryRouter initialEntries={["/p/ws/scans/scan1/live"]}>
      <Routes><Route path="/p/:workspace/scans/:scanId/live" element={<LiveTab />} /></Routes>
    </MemoryRouter>,
  );
}

// jsdom navigator.language 默认 en，LanguageDetector 会把 i18n 切到 en；现有断言依赖中文渲染，逐测试钉回 zh。
beforeEach(() => { i18n.changeLanguage("zh"); scanMetaState.meta = null; });

describe("LiveTab", () => {
  afterEach(() => vi.useRealTimers()); // fake timers 不泄漏到后续测试
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
    // 初始 tick 即 ~5000ms -> "5s"（< 60s 显示秒）。waitFor 等 tick 生效。
    await waitFor(() => {
      expect(screen.getByText(/^5s$/)).toBeInTheDocument();
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
    // DashboardPanel 渲染 current_phase（font-semibold text-primary 是头条 phase 标签）
    await waitFor(() => {
      const matches = screen.getAllByText(/vulnerability-analysis/);
      expect(matches.some((el) => el.classList.contains("font-semibold"))).toBe(true);
    });
  });

  // ── 问题 1：8h 时差修复（ts 归一化）──
  it("elapsed 用无时区 ts（生产 ndjson）当 UTC 解析，不漂 8h", async () => {
    // worker 容器 UTC 墙钟写 "YYYY-MM-DD HH:MM:SS"（无时区）。
    // 5 秒前的 UTC 时刻，手写成无时区空格分隔串模拟生产 ndjson。
    const past = new Date(Date.now() - 5000);
    const pad = (n: number) => String(n).padStart(2, "0");
    const noTzTs = `${past.getUTCFullYear()}-${pad(past.getUTCMonth() + 1)}-${pad(past.getUTCDate())} ${pad(past.getUTCHours())}:${pad(past.getUTCMinutes())}:${pad(past.getUTCSeconds())}`;
    eventsState.events = [
      { type: "PhaseEvent", event: "start", phase: "recon", steps: [], step_intents: [], ts: noTzTs, category: "PHASE" },
    ];
    eventsState.status = "open";
    renderLive();
    // 5s 前的事件 -> elapsed ≈ 5s（非 8h+5s）。
    await waitFor(() => {
      expect(screen.getByText(/^5s$/)).toBeInTheDocument();
    });
    // 关键：绝不出现 "8h"（漂移标志）
    expect(screen.queryByText(/8h/)).not.toBeInTheDocument();
  });

  // ── 问题 2：getScan 接通开始时间 / 总耗时 ──
  it("getScan 返回 created_at -> 渲染开始时间 + 总耗时", async () => {
    const startedUnix = Math.floor(Date.now() / 1000) - 10; // 10 秒前开始
    scanMetaState.meta = { created_at: startedUnix, completed_at: null, metrics: {} };
    eventsState.events = [];
    eventsState.status = "open";
    renderLive();
    await waitFor(() => {
      // 开始时间 label 出现
      expect(screen.getByText(/开始时间/)).toBeInTheDocument();
      // 总耗时 label 出现
      expect(screen.getByText(/总耗时/)).toBeInTheDocument();
    });
  });

  // ── 治本：跨时钟源相减校正（server_now offset）──
  it("服务端时钟领先浏览器时，总耗时经 offset 校正显示真实正值", async () => {
    vi.useFakeTimers();
    const browserNowMs = Date.UTC(2026, 7, 12, 2, 49, 45);
    vi.setSystemTime(browserNowMs);
    const browserSec = Math.floor(browserNowMs / 1000);
    // 服务端时钟领先浏览器 10s；扫描在服务端 5 秒前创建。真实总耗时 = 5s。
    // 旧逻辑（裸 Date.now - created_at）= -5s；仅 fmtMs clamp = 0s（失真）；offset 校正 = 5s（正确）。
    scanMetaState.meta = {
      created_at: browserSec + 5, server_now: browserSec + 10,
      completed_at: null, metrics: {},
    };
    eventsState.events = [];
    eventsState.status = "open";
    renderLive();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    // 校正后总耗时 = 5s（非 -5s / 非 0s），且面板无任何负数耗时串
    expect(screen.getByText(/^5s$/)).toBeInTheDocument();
    // /^-\d/ 排除 current_phase 占位 "-"（null → "-"），只匹配 "-5s" 这类负数耗时
    expect(screen.queryAllByText(/^-\d/)).toEqual([]);
    vi.useRealTimers();
  });

  it("getScan 失败时降级（不阻塞 live 页，仍渲染 LogStream）", () => {
    scanMetaState.meta = null;
    eventsState.events = [];
    eventsState.status = "open";
    renderLive();
    expect(document.querySelector('[aria-live="polite"]')).toBeInTheDocument();
  });

  // ── 问题 3：SSE 实时性指示 ──
  it("渲染 SSE 连接态 + 最后事件秒前 + 事件计数", () => {
    const pastTs = new Date(Date.now() - 4000).toISOString(); // 4 秒前
    eventsState.events = [
      { type: "PhaseEvent", event: "start", phase: "recon", steps: [], step_intents: [], ts: pastTs, category: "PHASE" },
      { type: "AgentEvent", event: "start", agent_name: "recon", attempt: 1, ts: pastTs, category: "AGENT" },
    ];
    eventsState.status = "open";
    renderLive();
    expect(screen.getByText(/已连接/)).toBeInTheDocument();
    // 事件计数 = 2（精确匹配 "事件 2" / "events 2"，避免 /2/ 误匹配 lastEvent 秒数）
    expect(screen.getByText(/事件\s*2|events\s*2/)).toBeInTheDocument();
  });

  // ── 问题 4：扫描完成后阶段耗时停止计时（不再每秒增长）──
  it("扫描完成后阶段耗时定格，不再计时（scan_end 时刻 - phaseStart）", async () => {
    vi.useFakeTimers();
    const now = Date.now();
    vi.setSystemTime(now);
    // phase start 5 分钟前；scan_end completed 100s 前 -> 定格应为 200s = "3m 20s"。
    // 走 endedCompleted 兜底（meta=null，不依赖后端 completed_at），验证 scan_end 一出现即停表。
    scanMetaState.meta = null;
    eventsState.events = [
      { type: "PhaseEvent", event: "start", phase: "recon", steps: [], step_intents: [], ts: new Date(now - 300_000).toISOString(), category: "PHASE" },
      { type: "scan_end", status: "completed", ts: new Date(now - 100_000).toISOString(), category: "CONTROL" },
    ];
    eventsState.status = "closed";

    renderLive();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });

    // 完成定格：elapsed = scan_end.ts - phaseStart = 200000ms = "3m 20s"。
    // 若 bug（无完成守卫，tick = now - phaseStart），会显示 "5m 0s" 且持续增长。
    expect(screen.getByText(/^3m 20s$/)).toBeInTheDocument();
    expect(screen.queryByText(/^5m/)).not.toBeInTheDocument();

    // 推进 5s：定格则保持 "3m 20s"；若仍 tick 会增长到 "5m 5s"。
    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(screen.getByText(/^3m 20s$/)).toBeInTheDocument();

    vi.useRealTimers();
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
