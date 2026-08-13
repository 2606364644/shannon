import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, waitFor, act, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import { ScanList } from "../ScanList";

// toast 在 ScanList 用于操作反馈；隔离避免 sonner 全局副作用。
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

// 捕获 useNavigate 调用（MemoryRouter 仍用 actual）。
const { navMock } = vi.hoisted(() => ({ navMock: vi.fn() }));
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navMock };
});

// === Fake EventSource（运行中卡建 SSE 推 currentPhase 用）===
const activeESUrls: string[] = [];
class FakeES {
  static last?: FakeES;
  onmessage: ((e: { data: string; lastEventId?: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  closed = false;
  constructor(public url: string) { activeESUrls.push(url); FakeES.last = this; }
  emit(data: string, lastEventId?: string) { this.onmessage?.({ data, lastEventId }); }
  close() { this.closed = true; }
}
vi.stubGlobal("EventSource", FakeES);
const lastFakeES = () => FakeES.last;

// === Fixtures ===
// 组合扫描：combined=true, bb_phase=pending（白盒段中）, progress_pct=62
const combinedPending = {
  scan_id: "c1", scan_type: "whitebox", status: "running", created_at: 1000,
  completed_at: null, vuln_count: 0, total_cost_usd: 1.5, cost_currency: "USD",
  is_running: true, workflow_id: "ws-c1",
  combined: true, bb_phase: "pending", progress_pct: 62,
} as const;
// 组合扫描：黑盒段中
const combinedRunning = {
  scan_id: "c2", scan_type: "whitebox", status: "running", created_at: 2000,
  completed_at: null, vuln_count: 0, total_cost_usd: 3, cost_currency: "USD",
  is_running: true, workflow_id: "ws-c2",
  combined: true, bb_phase: "running", progress_pct: 88,
} as const;
// 纯白盒运行中（统一徽标：现在也显示进度）
const pureWhitebox = {
  scan_id: "w1", scan_type: "whitebox", status: "running", created_at: 3000,
  completed_at: null, vuln_count: 0, total_cost_usd: 0.5, cost_currency: "USD",
  is_running: true, workflow_id: "ws-w1", progress_pct: 40,
} as const;
// 纯黑盒运行中
const pureBlackbox = {
  scan_id: "b1", scan_type: "blackbox", status: "running", created_at: 4000,
  completed_at: null, vuln_count: 0, total_cost_usd: 0.2, cost_currency: "USD",
  is_running: true, workflow_id: "ws-b1", progress_pct: 55,
} as const;
// 终态白盒（完成）
const completedWb = {
  scan_id: "d1", scan_type: "whitebox", status: "completed", created_at: 5000,
  completed_at: 6000, vuln_count: 3, total_cost_usd: 2, cost_currency: "USD",
  is_running: false, workflow_id: "ws-d1", progress_pct: 100,
} as const;

let listCalls = 0;
const server = setupServer(
  http.get("/api/workspaces/:ws/scans", () => { listCalls++; return HttpResponse.json([]); }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  i18n.changeLanguage("zh"); listCalls = 0; navMock.mockClear();
  activeESUrls.length = 0; FakeES.last = undefined;
});
afterEach(() => { server.resetHandlers(); cleanup(); vi.useRealTimers(); });
afterAll(() => server.close());

function renderList() {
  return render(
    <MemoryRouter initialEntries={["/p/ws"]}>
      <Routes><Route path="/p/:workspace" element={<ScanList />} /></Routes>
    </MemoryRouter>,
  );
}

function phaseStart(phase: string) {
  return JSON.stringify({
    ts: "2026-08-13T00:00:00.000Z", category: "PHASE", type: "PhaseEvent",
    phase, event: "start", steps: [], step_intents: [],
  });
}
function scanEnd(status: string) {
  return JSON.stringify({
    ts: "2026-08-13T00:00:00.000Z", category: "CONTROL", type: "scan_end", status,
  });
}

describe("ScanList 卡片 - 统一进度徽标（所有运行中卡）", () => {
  it("组合 pending: 62% + 白盒扫描中 + progressbar valuenow=62", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([combinedPending])));
    renderList();
    await waitFor(() => expect(screen.getByText("ws-c1")).toBeInTheDocument());
    expect(screen.getByText("62%")).toBeInTheDocument();
    expect(screen.getByText("白盒扫描中")).toBeInTheDocument();
    const bar = document.querySelector('[role="progressbar"]');
    expect(bar?.getAttribute("aria-valuenow")).toBe("62");
  });

  it("组合 running: 88% + 黑盒扫描中", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([combinedRunning])));
    renderList();
    await waitFor(() => expect(screen.getByText("ws-c2")).toBeInTheDocument());
    expect(screen.getByText("88%")).toBeInTheDocument();
    expect(screen.getByText("黑盒扫描中")).toBeInTheDocument();
  });

  it("纯白盒运行中: 显示 % + 进度条 + 白盒段标签（统一徽标，不再无进度）", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([pureWhitebox])));
    renderList();
    await waitFor(() => expect(screen.getByText("ws-w1")).toBeInTheDocument());
    expect(screen.getByText("40%")).toBeInTheDocument();
    expect(screen.getByText("白盒")).toBeInTheDocument();
    expect(document.querySelector('[role="progressbar"]')).toBeTruthy();
  });

  it("纯黑盒运行中: 显示 % + 黑盒段标签", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([pureBlackbox])));
    renderList();
    await waitFor(() => expect(screen.getByText("ws-b1")).toBeInTheDocument());
    expect(screen.getByText("55%")).toBeInTheDocument();
    expect(screen.getByText("黑盒")).toBeInTheDocument();
  });

  it("终态(完成)卡不显示进度徽标（无 progressbar 无 %）", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([completedWb])));
    renderList();
    await waitFor(() => expect(screen.getByText("ws-d1")).toBeInTheDocument());
    expect(document.querySelector('[role="progressbar"]')).toBeNull();
    expect(screen.queryByText(/\d+%/)).not.toBeInTheDocument();
  });

  it("组合卡无展开按钮（步级移至详情页）", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([combinedPending])));
    renderList();
    await waitFor(() => expect(screen.getByText("ws-c1")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /展开/ })).not.toBeInTheDocument();
  });
});

describe("ScanList 运行中卡 - 按需 SSE 推实时阶段", () => {
  it("纯白盒运行中卡挂载即建 SSE（scanEventsUrl 含 /events）", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([pureWhitebox])));
    renderList();
    await waitFor(() => expect(screen.getByText("ws-w1")).toBeInTheDocument());
    await waitFor(() => expect(activeESUrls.some((u) => u.includes("/events"))).toBe(true));
  });

  it("SSE PhaseEvent 推 currentPhase → 纯白盒段标签带 phase（白盒 · recon）", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([pureWhitebox])));
    renderList();
    await waitFor(() => expect(screen.getByText("ws-w1")).toBeInTheDocument());
    await waitFor(() => expect(lastFakeES()).toBeDefined());
    act(() => { lastFakeES()!.emit(phaseStart("recon")); });
    await waitFor(() => expect(screen.getByText(/recon/)).toBeInTheDocument());
    // 段标签含白盒 + recon（"白盒 · recon"）
    expect(screen.getByText(/白盒/)).toBeInTheDocument();
  });

  it("终态卡不建 SSE", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([completedWb])));
    renderList();
    await waitFor(() => expect(screen.getByText("ws-d1")).toBeInTheDocument());
    expect(activeESUrls).toHaveLength(0);
  });

  it("scan_end completed → 重新 listScans 刷新（listCalls 增加）", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => { listCalls++; return HttpResponse.json([pureWhitebox]); }));
    renderList();
    await waitFor(() => expect(screen.getByText("ws-w1")).toBeInTheDocument());
    const callsBefore = listCalls;
    await waitFor(() => expect(lastFakeES()).toBeDefined());
    act(() => { lastFakeES()!.emit(scanEnd("completed")); });
    await waitFor(() => expect(listCalls).toBeGreaterThan(callsBefore));
  });
});

describe("ScanList 运行中卡 - 定时轮询刷新（x% 实时推进）", () => {
  it("运行中卡存在时每 10s 静默刷新 listScans（progress_pct 推进，不闪 Skeleton）", async () => {
    vi.useFakeTimers();
    server.use(http.get("/api/workspaces/:ws/scans", () => { listCalls++; return HttpResponse.json([pureWhitebox]); }));
    renderList();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText("ws-w1")).toBeInTheDocument();
    const callsAfterMount = listCalls;
    // 推进 10s → 静默 refresh（不 setLoading，SSE 不断、不闪 Skeleton）
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(listCalls).toBeGreaterThan(callsAfterMount);
    expect(screen.getByText("ws-w1")).toBeInTheDocument();
  });

  it("无运行中卡时不轮询（终态卡 listCalls 不随时间增加）", async () => {
    vi.useFakeTimers();
    server.use(http.get("/api/workspaces/:ws/scans", () => { listCalls++; return HttpResponse.json([completedWb]); }));
    renderList();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText("ws-d1")).toBeInTheDocument();
    const callsAfterMount = listCalls;
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(listCalls).toBe(callsAfterMount);
  });
});
