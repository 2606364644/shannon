import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { DeliverablesTab } from "./DeliverablesTab";

// 三种 merge_source + injection 无 queue + 空 files 的合并 fixture
function makeSummary(overrides: Partial<{
  aggregated_vulnerabilities: unknown[];
  files: unknown[];
  notes: Record<string, unknown>;
}> = {}) {
  return {
    track: "whitebox",
    files: overrides.files ?? [],
    aggregated_vulnerabilities: overrides.aggregated_vulnerabilities ?? [],
    notes: overrides.notes ?? {},
  };
}

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/p/:workspace/deliverables" element={<DeliverablesTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("DeliverablesTab", () => {
  it("渲染漏洞聚合网格标题 + vuln 行（结构性：删除 vuln 行则失败）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/deliverables", () =>
        HttpResponse.json(
          makeSummary({
            aggregated_vulnerabilities: [
              {
                ID: "SSRF-01",
                vulnerability_type: "URL_Manipulation",
                externally_exploitable: true,
                merge_source: "llm-only",
                confidence: "needs_review",
                source_endpoint: "GET /research",
              },
            ],
          }),
        ),
      ),
    );
    renderAt("/p/NodeGoat/deliverables");
    await waitFor(() => expect(screen.getByText("SSRF-01")).toBeInTheDocument());
    expect(screen.getByText(/漏洞聚合/)).toHaveTextContent("漏洞聚合 · 1");
    // 可达性 ● 徽章
    expect(screen.getByText(/可达/)).toBeInTheDocument();
    // confidence 徽章
    expect(screen.getByText("needs_review")).toBeInTheDocument();
    // source_endpoint
    expect(screen.getByText(/GET \/research/)).toBeInTheDocument();
  });

  it("三种 merge_source 值各渲染正确徽章（llm-only / gitnexus-only / both）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/deliverables", () =>
        HttpResponse.json(
          makeSummary({
            aggregated_vulnerabilities: [
              { ID: "A-01", vulnerability_type: "T1", externally_exploitable: false, merge_source: "llm-only" },
              { ID: "B-01", vulnerability_type: "T2", externally_exploitable: false, merge_source: "gitnexus-only" },
              { ID: "C-01", vulnerability_type: "T3", externally_exploitable: false, merge_source: "both" },
            ],
          }),
        ),
      ),
    );
    renderAt("/p/ws/deliverables");
    await waitFor(() => expect(screen.getByText("A-01")).toBeInTheDocument());
    expect(screen.getByText(/LLM轨/)).toBeInTheDocument();
    expect(screen.getByText(/GN轨/)).toBeInTheDocument();
    expect(screen.getByText(/双轨确认/)).toBeInTheDocument();
  });

  it("未知 merge_source 字符串走 other 分支（literal-guard 防回退）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/deliverables", () =>
        HttpResponse.json(
          makeSummary({
            aggregated_vulnerabilities: [
              { ID: "X-01", vulnerability_type: "TX", externally_exploitable: false, merge_source: "weird-value" },
            ],
          }),
        ),
      ),
    );
    renderAt("/p/ws/deliverables");
    await waitFor(() => expect(screen.getByText("X-01")).toBeInTheDocument());
    // 未知值走 trace badge 显示原值
    expect(screen.getByText("weird-value")).toBeInTheDocument();
    // 三种已知徽章都不应出现
    expect(screen.queryByText(/LLM轨/)).not.toBeInTheDocument();
    expect(screen.queryByText(/GN轨/)).not.toBeInTheDocument();
    expect(screen.queryByText(/双轨确认/)).not.toBeInTheDocument();
  });

  it("merge_source 缺失（undefined）不渲染徽章", async () => {
    server.use(
      http.get("/api/workspaces/:ws/deliverables", () =>
        HttpResponse.json(
          makeSummary({
            aggregated_vulnerabilities: [
              { ID: "N-01", vulnerability_type: "TN", externally_exploitable: false },
            ],
          }),
        ),
      ),
    );
    renderAt("/p/ws/deliverables");
    await waitFor(() => expect(screen.getByText("N-01")).toBeInTheDocument());
    expect(screen.queryByText(/LLM轨/)).not.toBeInTheDocument();
    expect(screen.queryByText(/双轨确认/)).not.toBeInTheDocument();
  });

  it("injection 无 queue 标注", async () => {
    server.use(
      http.get("/api/workspaces/:ws/deliverables", () =>
        HttpResponse.json(makeSummary({ notes: { injection_has_no_queue: true } })),
      ),
    );
    renderAt("/p/ws/deliverables");
    await waitFor(() => expect(screen.getByText(/injection 类无独立 queue/)).toBeInTheDocument());
  });

  it("空产物（聚合 0 + 无 injection 标注）显示空态", async () => {
    server.use(
      http.get("/api/workspaces/:ws/deliverables", () => HttpResponse.json(makeSummary())),
    );
    renderAt("/p/ws/deliverables");
    await waitFor(() => expect(screen.getByText(/漏洞聚合/)).toHaveTextContent("漏洞聚合 · 0"));
    expect(screen.getByText(/暂无聚合漏洞/)).toBeInTheDocument();
  });

  it("FileTree 点击文件触发 FilePreview：md kind 走 MarkdownView（请求 ?path=）", async () => {
    let mdRequested = false;
    server.use(
      http.get("/api/workspaces/:ws/deliverables", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("path")) {
          mdRequested = true;
          return new HttpResponse("# 报告标题", { headers: { "content-type": "text/plain" } });
        }
        return HttpResponse.json(
          makeSummary({
            files: [
              { path: "whitebox/injection_findings.md", size: 10, kind: "md" },
            ],
          }),
        );
      }),
    );
    renderAt("/p/ws/deliverables");
    await waitFor(() => expect(screen.getByText("injection_findings.md")).toBeInTheDocument());
    fireEvent.click(screen.getByText("injection_findings.md"));
    await waitFor(() => expect(mdRequested).toBe(true));
  });

  it("empty_json kind 文件点击显示『无数据（常态空）』不请求 ?path=", async () => {
    let pathRequested = false;
    server.use(
      http.get("/api/workspaces/:ws/deliverables", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("path")) {
          pathRequested = true;
          return new HttpResponse("[]", { headers: { "content-type": "text/plain" } });
        }
        return HttpResponse.json(
          makeSummary({
            files: [{ path: "whitebox/authz_gitnexus_queue.json", size: 2, kind: "empty_json" }],
          }),
        );
      }),
    );
    renderAt("/p/ws/deliverables");
    await waitFor(() => expect(screen.getByText("authz_gitnexus_queue.json")).toBeInTheDocument());
    fireEvent.click(screen.getByText("authz_gitnexus_queue.json"));
    await waitFor(() => expect(screen.getByText(/无数据/)).toBeInTheDocument());
    expect(pathRequested).toBe(false);
  });

  it("big_json kind 显示前 500 字符占位提示", async () => {
    server.use(
      http.get("/api/workspaces/:ws/deliverables", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("path")) {
          return new HttpResponse("{}".repeat(50), { headers: { "content-type": "text/plain" } });
        }
        return HttpResponse.json(
          makeSummary({
            files: [{ path: "whitebox/parameter_graph.json", size: 99999, kind: "big_json" }],
          }),
        );
      }),
    );
    renderAt("/p/ws/deliverables");
    await waitFor(() => expect(screen.getByText("parameter_graph.json")).toBeInTheDocument());
    fireEvent.click(screen.getByText("parameter_graph.json"));
    await waitFor(() => expect(screen.getByText(/大 JSON/)).toBeInTheDocument());
  });

  it("加载中显示 trace 占位", async () => {
    server.use(
      http.get("/api/workspaces/:ws/deliverables", async () => {
        await new Promise((r) => setTimeout(r, 50));
        return HttpResponse.json(makeSummary());
      }),
    );
    renderAt("/p/ws/deliverables");
    expect(screen.getByText(/加载产物/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/漏洞聚合/)).toBeInTheDocument());
  });
});
