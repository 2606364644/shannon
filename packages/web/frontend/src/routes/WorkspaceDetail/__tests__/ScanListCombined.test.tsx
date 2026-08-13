import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup, act } from "@testing-library/react";
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

// === Fake EventSource（on-demand 展开拉 events 用）===
// 对齐 useEventSource.test.ts：构造后暴露 last 实例，手动 emit message。
const activeESUrls: string[] = [];

class FakeES {
  static last?: FakeES;
  onmessage: ((e: { data: string; lastEventId?: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    activeESUrls.push(url);
    FakeES.last = this;
  }
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

// 纯白盒：无 combined 字段（回归）
const pureWhitebox = {
  scan_id: "w1", scan_type: "whitebox", status: "running", created_at: 3000,
  completed_at: null, vuln_count: 0, total_cost_usd: 0.5, cost_currency: "USD",
  is_running: true, workflow_id: "ws-w1",
} as const;

let listCalls = 0;
const server = setupServer(
  http.get("/api/workspaces/:ws/scans", () => { listCalls++; return HttpResponse.json([]); }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => { i18n.changeLanguage("zh"); listCalls = 0; navMock.mockClear(); activeESUrls.length = 0; FakeES.last = undefined; });
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderList() {
  return render(
    <MemoryRouter initialEntries={["/p/ws"]}>
      <Routes><Route path="/p/:workspace" element={<ScanList />} /></Routes>
    </MemoryRouter>,
  );
}

// 事件 ndjson 行：PhaseEvent(start) 声明 steps；StepEvent(complete) 标记完成。
function phaseStart(phase: string, steps: string[], intents: string[] = []) {
  return JSON.stringify({
    ts: "2026-08-13T00:00:00.000Z", category: "PHASE", type: "PhaseEvent",
    phase, event: "start", steps, step_intents: intents,
  });
}
function stepComplete(name: string, phase: string) {
  return JSON.stringify({
    ts: "2026-08-13T00:00:01.000Z", category: "STEP", type: "StepEvent",
    name, phase, event: "complete",
  });
}

describe("ScanList 组合扫描卡片 - 收起态", () => {
  it("组合扫描显示 progress_pct% + 进度条 + 阶段名（bb_phase=pending → 白盒扫描中）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([combinedPending])),
    );
    renderList();
    await waitFor(() => expect(screen.getByText("ws-c1")).toBeInTheDocument());
    // 62%
    expect(screen.getByText("62%")).toBeInTheDocument();
    // 阶段名（pending → 白盒扫描中）
    expect(screen.getByText("白盒扫描中")).toBeInTheDocument();
    // 进度条（role="progressbar" 或 aria-valuenow）
    const bar = document.querySelector('[role="progressbar"]');
    expect(bar).toBeTruthy();
    expect(bar?.getAttribute("aria-valuenow")).toBe("62");
  });

  it("bb_phase=running → 阶段名「黑盒扫描中」", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([combinedRunning])),
    );
    renderList();
    await waitFor(() => expect(screen.getByText("ws-c2")).toBeInTheDocument());
    expect(screen.getByText("88%")).toBeInTheDocument();
    expect(screen.getByText("黑盒扫描中")).toBeInTheDocument();
  });

  it("组合卡片有展开按钮（步级）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([combinedPending])),
    );
    renderList();
    await waitFor(() => expect(screen.getByText("ws-c1")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /展开/ })).toBeInTheDocument();
  });
});

describe("ScanList 组合扫描卡片 - 展开态（按需步级）", () => {
  it("点展开 → 拉 SSE events → 显示步级进度（recon 4/6）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([combinedPending])),
    );
    renderList();
    await waitFor(() => expect(screen.getByText("ws-c1")).toBeInTheDocument());

    // 展开前：未发 SSE（按需，非 eager）
    expect(activeESUrls).toHaveLength(0);
    // 展开前：不显示步级 "recon"
    expect(screen.queryByText(/recon/)).not.toBeInTheDocument();

    // 点展开
    fireEvent.click(screen.getByRole("button", { name: /展开/ }));

    // SSE 连接建立（scanEventsUrl 路径含 /events）
    await waitFor(() => expect(activeESUrls.some((u) => u.includes("/events"))).toBe(true));
    expect(lastFakeES()).toBeDefined();

    // 喂 events：recon 阶段 6 steps，完成 4 个
    act(() => {
      lastFakeES()!.emit(phaseStart("recon", [
        "pre-recon", "route-map", "framework-scan", "endpoint-collect",
        "model-build", "summary",
      ]));
      lastFakeES()!.emit(stepComplete("pre-recon", "recon"));
      lastFakeES()!.emit(stepComplete("route-map", "recon"));
      lastFakeES()!.emit(stepComplete("framework-scan", "recon"));
      lastFakeES()!.emit(stepComplete("endpoint-collect", "recon"));
    });

    // 步级显示 "recon" + 进度 "4/6"
    await waitFor(() => {
      expect(screen.getByText(/recon/)).toBeInTheDocument();
      expect(screen.getByText("4/6")).toBeInTheDocument();
    });
  });

  it("再点收起 → 关 SSE 连接", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([combinedPending])),
    );
    renderList();
    await waitFor(() => expect(screen.getByText("ws-c1")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /展开/ }));
    await waitFor(() => expect(lastFakeES()).toBeDefined());
    expect(lastFakeES()!.closed).toBe(false);

    // 收起
    fireEvent.click(screen.getByRole("button", { name: /收起/ }));
    expect(lastFakeES()!.closed).toBe(true);
  });
});

describe("ScanList 纯白盒/纯黑盒 - 零回归", () => {
  it("纯白盒卡片：无 progress_pct、无进度条、无展开按钮（原单段卡片不变）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([pureWhitebox])),
    );
    renderList();
    await waitFor(() => expect(screen.getByText("ws-w1")).toBeInTheDocument());
    // 无展开按钮
    expect(screen.queryByRole("button", { name: /展开/ })).not.toBeInTheDocument();
    // 无进度条
    expect(document.querySelector('[role="progressbar"]')).toBeNull();
    // 无 % 显示
    expect(screen.queryByText(/\d+%/)).not.toBeInTheDocument();
  });
});
