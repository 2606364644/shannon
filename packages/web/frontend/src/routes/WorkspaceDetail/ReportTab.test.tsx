import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes, Outlet } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { SWRConfig } from "swr";
import i18n from "@/i18n";
import { ReportTab } from "./ReportTab";
import { downloadTextFile } from "@/lib/download";

// 下载器单测在 lib/download.test.ts（真 Blob/URL）；此处 mock 以断言组件接线
// （md 全文 + 文件名），reportDownloadFilename 保持真实现（文件名规则被覆盖）。
vi.mock("@/lib/download", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/download")>();
  return { ...actual, downloadTextFile: vi.fn() };
});
beforeEach(() => vi.mocked(downloadTextFile).mockClear());

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
// jsdom navigator.language 默认 en，LanguageDetector 会把 i18n 切到 en；现有断言依赖中文渲染，逐测试钉回 zh。
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const MD = `# 综合安全评估报告

## 执行摘要
1. SSRF-01 漏洞示例
`;

// report_data.json fixture（spec 2026-08-26 §4 schema，snake_case 直传）
const REPORT_DATA = {
  schema_version: 1,
  scan: { id: "scan1", track: "whitebox", repo: "NodeGoat" },
  executive_summary: {
    narrative: "应用暴露面集中在备忘录模块",
    risk_level: "极高",
    top_risks: [{ vuln_id: "XSS-VULN-01", reason: "公网可达", priority: "P0" }],
    remediation_order: null,
  },
  stats: {
    by_type: { xss: { count: 1, severity_range: "high", key_findings: null } },
    by_severity: { critical: 0, high: 1, medium: 0, low: 0 },
  },
  vulnerabilities: [
    {
      id: "XSS-VULN-01", type: "xss", title: "备忘录存储型 XSS", severity: "high",
      merge_source: "both", merged_from: [], endpoints: [], affected_entries: [],
      dataflow_steps: [], attack_chain_refs: [],
      poc: { request: { method: "POST", url: "http://t/memos", headers: {}, body: "memo=<img>" }, curl: "curl -X POST 'http://t/memos'" },
    },
  ],
  attack_chains: [],
  qa: { passed: true, checks: [], reworked_ids: [] },
};

// 默认 handler（旧 scan 降级形态）：scan 详情非组合 + report-data 404 → 走 md 渲染路径。
beforeEach(() => {
  server.use(
    http.get("/api/workspaces/:ws/scans/:scanId", () =>
      HttpResponse.json({ combined: false, scan_type: "whitebox", status: "completed" })),
    http.get("/api/workspaces/:ws/scans/:scanId/report-data", () =>
      HttpResponse.json({ detail: "report data not generated" }, { status: 404 })),
  );
});

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      {/* SWR 迁移适配（spec §6.5）：独立 cache，防跨测试缓存污染（各测试 msw payload 不同）。 */}
      <SWRConfig value={{ provider: () => new Map() }}>
        <Routes>
          <Route path="/p/:workspace/scans/:scanId/report" element={<ReportTab />} />
        </Routes>
      </SWRConfig>
    </MemoryRouter>,
  );
}

/** 组合报告渲染（ReportTab 读 useOutletContext.selectedRun，须挂 Outlet context 下）。 */
function renderCombinedAt(path: string, ctx: Record<string, unknown>) {
  const CtxOutlet = () => <Outlet context={ctx} />;
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SWRConfig value={{ provider: () => new Map() }}>
        <Routes>
          <Route path="/p" element={<CtxOutlet />}>
            <Route path=":workspace/scans/:scanId/report" element={<ReportTab />} />
          </Route>
        </Routes>
      </SWRConfig>
    </MemoryRouter>,
  );
}

describe("ReportTab（md 降级路径——旧 scan 无 report_data.json）", () => {
  it("report-data 404 → 回退 GET /report（text/plain）→ 经 MarkdownView 渲染（标题 H1 出现）", async () => {
    let reportDataHits = 0;
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/report-data", () => {
        reportDataHits += 1;
        return HttpResponse.json({ detail: "report data not generated" }, { status: 404 });
      }),
      http.get("/api/workspaces/:ws/scans/:scanId/report", () =>
        new HttpResponse(MD, { headers: { "content-type": "text/plain" } })),
    );
    renderAt("/p/ws/scans/scan1/report");
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1, name: /综合安全评估报告/ })).toBeInTheDocument(),
    );
    expect(reportDataHits).toBe(1); // 确为「先 report-data → 404 → md」分流
  });

  it("报告渲染后点「下载 .md」→ downloadTextFile 收 md 全文 + 单报告文件名", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/report", () =>
        new HttpResponse(MD, { headers: { "content-type": "text/plain" } })),
    );
    renderAt("/p/ws/scans/scan1/report");
    fireEvent.click(await screen.findByRole("button", { name: /下载 \.md/ }));
    expect(vi.mocked(downloadTextFile)).toHaveBeenCalledWith("scan1-report.md", MD);
  });

  it("加载中渲染 Skeleton（animate-pulse）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/report", async () => {
        await new Promise((r) => setTimeout(r, 50));
        return new HttpResponse(MD, { headers: { "content-type": "text/plain" } });
      }),
    );
    renderAt("/p/ws/scans/scan1/report");
    expect(document.querySelector(".animate-pulse")).toBeInTheDocument();
    // 等到加载完成确保不泄漏 act warning
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument(),
    );
  });

  it("report-data 非 404 失败（500）→ ErrorState，不静默回退 md", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/report-data", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 })),
      http.get("/api/workspaces/:ws/scans/:scanId/report", () =>
        new HttpResponse(MD, { headers: { "content-type": "text/plain" } })),
    );
    renderAt("/p/ws/scans/scan1/report");
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    // 守卫：未走 md 降级（结构化端点 5xx 是错误态，非「旧 scan」信号）
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
  });

  it("两个端点都缺（404/空 md）→ Empty 而非加载态", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/report", () =>
        new HttpResponse("", { headers: { "content-type": "text/plain" } })),
    );
    renderAt("/p/ws/scans/scan1/report");
    await waitFor(() => expect(screen.getByText(/报告尚未生成/)).toBeInTheDocument());
    // 守卫：不渲染 Skeleton 加载态
    expect(document.querySelector(".animate-pulse")).not.toBeInTheDocument();
  });
});

describe("ReportTab（report-data 结构化优先路径）", () => {
  it("report-data 200 → ReportView 纯渲染（漏洞卡 + 摘要），不请求 md", async () => {
    let mdHits = 0;
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/report-data", () =>
        HttpResponse.json(REPORT_DATA)),
      http.get("/api/workspaces/:ws/scans/:scanId/report", () => {
        mdHits += 1;
        return new HttpResponse(MD, { headers: { "content-type": "text/plain" } });
      }),
    );
    renderAt("/p/ws/scans/scan1/report");
    await waitFor(() =>
      expect(screen.getByTestId("structured-report")).toBeInTheDocument());
    expect(screen.getByTestId("report-view")).toBeInTheDocument();
    // ID 同时出现在摘要锚点与卡片头
    expect(screen.getAllByText("XSS-VULN-01").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/应用暴露面集中在备忘录模块/)).toBeInTheDocument();
    expect(screen.getByTestId("vuln-poc").textContent).toContain("curl -X POST 'http://t/memos'");
    await new Promise((r) => setTimeout(r, 20));
    expect(mdHits).toBe(0); // 结构化命中即不走 md
  });

  it("黑盒 scan（scan_type=blackbox）→ report-data 带 track=blackbox", async () => {
    let blackboxHits = 0;
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId", () =>
        HttpResponse.json({ combined: false, scan_type: "blackbox", status: "completed" })),
      http.get("/api/workspaces/:ws/scans/:scanId/report-data", ({ request }) => {
        if (new URL(request.url).searchParams.get("track") === "blackbox") blackboxHits += 1;
        return HttpResponse.json(REPORT_DATA);
      }),
    );
    renderAt("/p/ws/scans/scan1/report");
    await waitFor(() => expect(screen.getByTestId("structured-report")).toBeInTheDocument());
    expect(blackboxHits).toBe(1);
  });

  it("结构化路径点「下载 .md」→ 按需拉 md 后 downloadTextFile", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/report-data", () =>
        HttpResponse.json(REPORT_DATA)),
      http.get("/api/workspaces/:ws/scans/:scanId/report", () =>
        new HttpResponse(MD, { headers: { "content-type": "text/plain" } })),
    );
    renderAt("/p/ws/scans/scan1/report");
    fireEvent.click(await screen.findByRole("button", { name: /下载 \.md/ }));
    await waitFor(() =>
      expect(vi.mocked(downloadTextFile)).toHaveBeenCalledWith("scan1-report.md", MD));
  });

  it("组合扫描融合子 tab：selectedRun → run 级 report-data?track=combined 结构化渲染", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId", () =>
        HttpResponse.json({ combined: true, scan_type: "whitebox", status: "completed" })),
      http.get("/api/workspaces/:ws/scans/:scanId/blackbox-runs/:runId/report-data", () =>
        HttpResponse.json({ ...REPORT_DATA, scan: { ...REPORT_DATA.scan, track: "combined" } })),
    );
    renderCombinedAt("/p/ws/scans/scan1/report", { selectedRun: "run-1", runSummary: null });
    await waitFor(() => expect(screen.getByTestId("structured-report")).toBeInTheDocument());
    expect(screen.getAllByText("XSS-VULN-01").length).toBeGreaterThanOrEqual(2);
    // 三子 tab 仍呈现
    expect(screen.getByRole("tab", { name: /融合报告/ })).toBeInTheDocument();
  });
});

describe("ReportTab 版心不变量（拉宽≠满宽）", () => {
  /** 报告是长文档型页面：所有分支（结构化/md 降级/组合）的正文都须包在居中版心列内
   *  ——版心是这页布局的骨架（卡片边框/表格/散文/POC 共享同一宽度），删掉版心会让
   *  各元素宽度各自为政（散文护栏 768px vs 卡片满宽 → 左重右空）。版心档位 1536px：
   *  endpoints 表 7 列 mono（Path/Source/Sink 等 file:line）自然需求 ~1300px+，1280
   *  下挤、1536 舒展（2026-08-26 定档）。 */
  const expectInColumn = () => {
    const col = document.querySelector('[data-testid="report-page-column"]');
    expect(col).not.toBeNull();
    expect(col!.className).toContain("mx-auto");
    expect(col!.className).toContain("max-w-[1536px]");
    return col!;
  };

  it("结构化路径：正文包在 mx-auto max-w-[1536px] 版心列内", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/report-data", () =>
        HttpResponse.json(REPORT_DATA)),
    );
    renderAt("/p/ws/scans/scan1/report");
    await waitFor(() => expect(screen.getByTestId("structured-report")).toBeInTheDocument());
    const col = expectInColumn();
    expect(col.contains(screen.getByTestId("structured-report"))).toBe(true);
  });

  it("md 降级路径：正文同样包在版心列内（MarkdownView 内部 max-w-none，靠版心守行宽）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/report", () =>
        new HttpResponse(MD, { headers: { "content-type": "text/plain" } })),
    );
    renderAt("/p/ws/scans/scan1/report");
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1, name: /综合安全评估报告/ })).toBeInTheDocument(),
    );
    const col = expectInColumn();
    expect(col.contains(screen.getByRole("heading", { level: 1 }))).toBe(true);
  });

  it("组合视图：三子 tab 条 + 正文都在版心列内", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId", () =>
        HttpResponse.json({ combined: true, scan_type: "whitebox", status: "completed" })),
      http.get("/api/workspaces/:ws/scans/:scanId/blackbox-runs/:runId/report-data", () =>
        HttpResponse.json({ ...REPORT_DATA, scan: { ...REPORT_DATA.scan, track: "combined" } })),
    );
    renderCombinedAt("/p/ws/scans/scan1/report", { selectedRun: "run-1", runSummary: null });
    await waitFor(() => expect(screen.getByTestId("structured-report")).toBeInTheDocument());
    const col = expectInColumn();
    expect(col.contains(screen.getByRole("tab", { name: /融合报告/ }))).toBe(true);
    expect(col.contains(screen.getByTestId("structured-report"))).toBe(true);
  });
});

describe("ReportTab i18n", () => {
  afterEach(() => i18n.changeLanguage("zh"));

  it("切英文后空态标题变英文", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/report", () =>
        new HttpResponse("", { headers: { "content-type": "text/plain" } })),
    );
    renderAt("/p/ws/scans/scan1/report");
    await screen.findByText(/报告尚未生成/);
    await i18n.changeLanguage("en");
    expect(await screen.findByText("Report not generated yet")).toBeInTheDocument();
    expect(screen.getByText(/Will appear here once the scan completes/)).toBeInTheDocument();
  });

  it("报告正文 Markdown 内容不随语言变化（数据不动）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/report", () =>
        new HttpResponse(MD, { headers: { "content-type": "text/plain" } })),
    );
    renderAt("/p/ws/scans/scan1/report");
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1, name: /综合安全评估报告/ })).toBeInTheDocument(),
    );
    await i18n.changeLanguage("en");
    // 报告正文仍是中文（LLM 生成的数据，不受语言切换影响）
    expect(screen.getByRole("heading", { level: 1, name: /综合安全评估报告/ })).toBeInTheDocument();
  });
});
