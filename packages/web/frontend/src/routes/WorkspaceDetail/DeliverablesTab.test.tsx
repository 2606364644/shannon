import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { SWRConfig } from "swr";
import i18n from "@/i18n";
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
// jsdom navigator.language 默认 en，LanguageDetector 会把 i18n 切到 en；现有断言依赖中文渲染，逐测试钉回 zh。
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      {/* SWR 迁移适配（spec §6.5）：独立 cache，防跨测试缓存污染。 */}
      <SWRConfig value={{ provider: () => new Map() }}>
        <Routes>
          <Route path="/p/:workspace/scans/:scanId/deliverables" element={<DeliverablesTab />} />
        </Routes>
      </SWRConfig>
    </MemoryRouter>,
  );
}

describe("DeliverablesTab", () => {
  it("渲染漏洞聚合网格标题 + vuln 行（结构性：删除 vuln 行则失败）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", () =>
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
    renderAt("/p/NodeGoat/scans/scan1/deliverables");
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
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", () =>
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
    renderAt("/p/ws/scans/scan1/deliverables");
    await waitFor(() => expect(screen.getByText("A-01")).toBeInTheDocument());
    expect(screen.getByText(/LLM 轨/)).toBeInTheDocument();
    expect(screen.getByText(/GN 轨/)).toBeInTheDocument();
    expect(screen.getByText(/双轨确认/)).toBeInTheDocument();
  });

  it("未知 merge_source 字符串走 other 分支（literal-guard 防回退）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", () =>
        HttpResponse.json(
          makeSummary({
            aggregated_vulnerabilities: [
              { ID: "X-01", vulnerability_type: "TX", externally_exploitable: false, merge_source: "weird-value" },
            ],
          }),
        ),
      ),
    );
    renderAt("/p/ws/scans/scan1/deliverables");
    await waitFor(() => expect(screen.getByText("X-01")).toBeInTheDocument());
    // 未知值走 trace badge 显示原值
    expect(screen.getByText("weird-value")).toBeInTheDocument();
    // 三种已知徽章都不应出现
    expect(screen.queryByText(/LLM 轨/)).not.toBeInTheDocument();
    expect(screen.queryByText(/GN 轨/)).not.toBeInTheDocument();
    expect(screen.queryByText(/双轨确认/)).not.toBeInTheDocument();
  });

  it("merge_source 缺失（undefined）不渲染徽章", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", () =>
        HttpResponse.json(
          makeSummary({
            aggregated_vulnerabilities: [
              { ID: "N-01", vulnerability_type: "TN", externally_exploitable: false },
            ],
          }),
        ),
      ),
    );
    renderAt("/p/ws/scans/scan1/deliverables");
    await waitFor(() => expect(screen.getByText("N-01")).toBeInTheDocument());
    expect(screen.queryByText(/LLM 轨/)).not.toBeInTheDocument();
    expect(screen.queryByText(/双轨确认/)).not.toBeInTheDocument();
  });

  it("injection 无 queue 用 Badge + 原生 title tooltip（不暴露裸 queue 文案）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", () =>
        HttpResponse.json(makeSummary({ notes: { injection_has_no_queue: true } })),
      ),
    );
    renderAt("/p/ws/scans/scan1/deliverables");
    await waitFor(() => expect(screen.getByText(/injection 类/)).toBeInTheDocument());
    // Badge 提供原生 title tooltip（不暴露实现细节的裸 queue 文案给普通断言）
    const badge = screen.getByText(/injection 类/).closest("[title]");
    expect(badge).not.toBeNull();
    expect(badge!.getAttribute("title")).toMatch(/injection/);
    // 关键守卫：不再出现裸的『无独立 queue』文案（实现细节已藏进 title）
    expect(screen.queryByText(/无独立 queue/)).not.toBeInTheDocument();
  });

  it("空产物（聚合 0 + 无 injection 标注）显示空态组件", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", () => HttpResponse.json(makeSummary())),
    );
    renderAt("/p/ws/scans/scan1/deliverables");
    await waitFor(() => expect(screen.getByText(/漏洞聚合/)).toHaveTextContent("漏洞聚合 · 0"));
    // Empty 组件渲染『暂无聚合漏洞』标题
    expect(screen.getByText(/暂无聚合漏洞/)).toBeInTheDocument();
  });

  it("FileTree 点击文件触发 FilePreview：md kind 走 MarkdownView（请求 ?path=）", async () => {
    let mdRequested = false;
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", ({ request }) => {
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
    renderAt("/p/ws/scans/scan1/deliverables");
    await waitFor(() => expect(screen.getByText("injection_findings.md")).toBeInTheDocument());
    fireEvent.click(screen.getByText("injection_findings.md"));
    await waitFor(() => expect(mdRequested).toBe(true));
  });

  it("empty_json kind 文件点击显示『无数据（常态空）』不请求 ?path=", async () => {
    let pathRequested = false;
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", ({ request }) => {
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
    renderAt("/p/ws/scans/scan1/deliverables");
    await waitFor(() => expect(screen.getByText("authz_gitnexus_queue.json")).toBeInTheDocument());
    fireEvent.click(screen.getByText("authz_gitnexus_queue.json"));
    await waitFor(() => expect(screen.getByText(/无数据/)).toBeInTheDocument());
    expect(pathRequested).toBe(false);
  });

  it("big_json kind 显示『文件过大』提示（含字节数），无空 <pre> 占位", async () => {
    let pathRequested = false;
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("path")) {
          pathRequested = true;
          return new HttpResponse("{}".repeat(50), { headers: { "content-type": "text/plain" } });
        }
        return HttpResponse.json(
          makeSummary({
            files: [{ path: "whitebox/parameter_graph.json", size: 99999, kind: "big_json" }],
          }),
        );
      }),
    );
    const { container } = renderAt("/p/ws/scans/scan1/deliverables");
    await waitFor(() => expect(screen.getByText("parameter_graph.json")).toBeInTheDocument());
    fireEvent.click(screen.getByText("parameter_graph.json"));
    // 显示『文件过大』提示 + 字节数（size 来自 summary）
    await waitFor(() => expect(screen.getByText(/文件过大/)).toBeInTheDocument());
    expect(screen.getByText(/99999/)).toBeInTheDocument();
    // 守卫：不渲染空 <pre>（旧实现 content 永远为空 → <pre></pre>）
    const pres = container.querySelectorAll("pre");
    const emptyPres = Array.from(pres).filter((p) => p.textContent === "");
    expect(emptyPres.length).toBe(0);
    // 守卫：big_json 不发 ?path= 请求（无 range 支持，不浪费 fetch）
    expect(pathRequested).toBe(false);
  });

  it("llm_queue kind 点击触发 ?path= 请求并渲染文本（防 exploitation_queue 唯一守卫退化）", async () => {
    let pathRequested = false;
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("path")) {
          pathRequested = true;
          return new HttpResponse('[{"ID":"INJ-LLM"}]', { headers: { "content-type": "text/plain" } });
        }
        return HttpResponse.json(
          makeSummary({
            files: [{ path: "whitebox/injection_llm_queue.json", size: 20, kind: "llm_queue" }],
          }),
        );
      }),
    );
    renderAt("/p/ws/scans/scan1/deliverables");
    await waitFor(() => expect(screen.getByText("injection_llm_queue.json")).toBeInTheDocument());
    fireEvent.click(screen.getByText("injection_llm_queue.json"));
    await waitFor(() => expect(pathRequested).toBe(true));
    await waitFor(() => expect(screen.getByText(/INJ-LLM/)).toBeInTheDocument());
  });

  it("gitnexus_queue kind 点击触发 ?path= 请求并渲染文本（防 exploitation_queue 唯一守卫退化）", async () => {
    let pathRequested = false;
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("path")) {
          pathRequested = true;
          return new HttpResponse('[{"ID":"XSS-GN"}]', { headers: { "content-type": "text/plain" } });
        }
        return HttpResponse.json(
          makeSummary({
            files: [{ path: "whitebox/xss_gitnexus_queue.json", size: 20, kind: "gitnexus_queue" }],
          }),
        );
      }),
    );
    renderAt("/p/ws/scans/scan1/deliverables");
    await waitFor(() => expect(screen.getByText("xss_gitnexus_queue.json")).toBeInTheDocument());
    fireEvent.click(screen.getByText("xss_gitnexus_queue.json"));
    await waitFor(() => expect(pathRequested).toBe(true));
    await waitFor(() => expect(screen.getByText(/XSS-GN/)).toBeInTheDocument());
  });

  it("加载中显示 Skeleton 占位（不暴露 trace 文案）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", async () => {
        await new Promise((r) => setTimeout(r, 50));
        return HttpResponse.json(makeSummary());
      }),
    );
    const { container } = renderAt("/p/ws/scans/scan1/deliverables");
    // Skeleton 占位（animate-pulse）出现
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
    // 关键守卫：不暴露旧 trace『加载产物』文案
    expect(screen.queryByText(/加载产物/)).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/漏洞聚合/)).toBeInTheDocument());
  });

  it("产物聚合 fetch 失败渲染 ErrorState 错误态（不永久 loading）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    renderAt("/p/ws/scans/scan1/deliverables");
    await waitFor(() => expect(screen.getByText(/产物加载失败/)).toBeInTheDocument());
    // ErrorState 用 role="alert"
    expect(screen.getByRole("alert")).toBeInTheDocument();
    // 关键守卫：不渲染 Skeleton 占位
    expect(screen.queryByText(/加载产物/)).not.toBeInTheDocument();
  });

  it("文件预览 fetch 失败渲染局部 ErrorState（不整页崩，左侧 vuln grid 仍可用）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("path")) {
          return HttpResponse.json({ detail: "file boom" }, { status: 500 });
        }
        return HttpResponse.json(
          makeSummary({
            files: [{ path: "whitebox/injection_findings.md", size: 10, kind: "md" }],
          }),
        );
      }),
    );
    renderAt("/p/ws/scans/scan1/deliverables");
    await waitFor(() => expect(screen.getByText("injection_findings.md")).toBeInTheDocument());
    fireEvent.click(screen.getByText("injection_findings.md"));
    await waitFor(() => expect(screen.getByText(/文件加载失败/)).toBeInTheDocument());
    // 关键守卫：md kind fetch 失败时不卡在『加载…』占位
    expect(screen.queryByText(/^加载…$/)).not.toBeInTheDocument();
    // 关键守卫：FilePreview 错误是局部的，至少有一个 role="alert"（ErrorState 渲染）
    expect(screen.getAllByRole("alert").length).toBeGreaterThanOrEqual(1);
    // 守卫：左列聚合入口仍可见（页面主体未崩；FileStage 返回按钮也含「漏洞聚合」字样，
    // 用带计数的精确匹配避免歧义）
    expect(screen.getByText(/漏洞聚合 · 0/)).toBeInTheDocument();
  });

  it("默认舞台为聚合视图；选中文件切换为 FileStage，返回按钮回聚合视图", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("path")) {
          return new HttpResponse("# h1", { headers: { "content-type": "text/plain" } });
        }
        return HttpResponse.json(
          makeSummary({
            aggregated_vulnerabilities: [
              { ID: "V-01", vulnerability_type: "T", externally_exploitable: false },
            ],
            files: [{ path: "whitebox/a.md", size: 4, kind: "md" }],
          }),
        );
      }),
    );
    renderAt("/p/ws/scans/scan1/deliverables");
    // 默认：聚合视图在舞台，无文件舞台
    await waitFor(() => expect(screen.getByTestId("agg-view")).toBeInTheDocument());
    expect(screen.queryByTestId("file-stage")).not.toBeInTheDocument();
    expect(screen.getByText("V-01")).toBeInTheDocument();
    // 点文件 → FileStage（含完整路径 + 返回按钮），聚合视图退场
    fireEvent.click(screen.getByText("a.md"));
    await waitFor(() => expect(screen.getByTestId("file-stage")).toBeInTheDocument());
    expect(screen.getByText("whitebox/a.md")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /返回漏洞聚合/ })).toBeInTheDocument();
    expect(screen.queryByTestId("agg-view")).not.toBeInTheDocument();
    // 返回按钮 → 回聚合视图
    fireEvent.click(screen.getByRole("button", { name: /返回漏洞聚合/ }));
    await waitFor(() => expect(screen.getByTestId("agg-view")).toBeInTheDocument());
    expect(screen.queryByTestId("file-stage")).not.toBeInTheDocument();
  });

  it("左列「漏洞聚合」入口在选中文件后可点击回聚合视图", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("path")) {
          return new HttpResponse("# h1", { headers: { "content-type": "text/plain" } });
        }
        return HttpResponse.json(
          makeSummary({ files: [{ path: "whitebox/a.md", size: 4, kind: "md" }] }),
        );
      }),
    );
    renderAt("/p/ws/scans/scan1/deliverables");
    fireEvent.click(await screen.findByText("a.md"));
    await waitFor(() => expect(screen.getByTestId("file-stage")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /漏洞聚合 · 0/ }));
    await waitFor(() => expect(screen.getByTestId("agg-view")).toBeInTheDocument());
  });

  it("exploitation_queue 结构化渲染 VulnCard（解析 vulnerabilities[]）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("path")) {
          return new HttpResponse(
            JSON.stringify({ vulnerabilities: [{ ID: "INJ-Q1", vulnerability_type: "SQLi", externally_exploitable: true }] }),
            { headers: { "content-type": "text/plain" } },
          );
        }
        return HttpResponse.json(
          makeSummary({
            files: [{ path: "whitebox/injection_exploitation_queue.json", size: 60, kind: "exploitation_queue" }],
          }),
        );
      }),
    );
    renderAt("/p/ws/scans/scan1/deliverables");
    fireEvent.click(await screen.findByText("injection_exploitation_queue.json"));
    // VulnCard 渲染（红色 ID 文本）而非原始 JSON pre
    await waitFor(() => expect(screen.getByText("INJ-Q1")).toBeInTheDocument());
    expect(screen.getByText(/可达/)).toBeInTheDocument();
  });

  it("exploitation_queue 非法 JSON 回退原文 pre（不崩）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("path")) {
          return new HttpResponse("not-json", { headers: { "content-type": "text/plain" } });
        }
        return HttpResponse.json(
          makeSummary({
            files: [{ path: "whitebox/x_exploitation_queue.json", size: 8, kind: "exploitation_queue" }],
          }),
        );
      }),
    );
    renderAt("/p/ws/scans/scan1/deliverables");
    fireEvent.click(await screen.findByText("x_exploitation_queue.json"));
    await waitFor(() => expect(screen.getByText("not-json")).toBeInTheDocument());
  });

  it("other kind 显示不支持预览空态，不请求 ?path= 且无空 <pre>", async () => {
    let pathRequested = false;
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("path")) {
          pathRequested = true;
          return new HttpResponse("x", { headers: { "content-type": "text/plain" } });
        }
        return HttpResponse.json(
          makeSummary({ files: [{ path: "whitebox/logo.png", size: 100, kind: "other" }] }),
        );
      }),
    );
    const { container } = renderAt("/p/ws/scans/scan1/deliverables");
    fireEvent.click(await screen.findByText("logo.png"));
    await waitFor(() => expect(screen.getByText(/不支持在线预览/)).toBeInTheDocument());
    expect(pathRequested).toBe(false);
    const emptyPres = Array.from(container.querySelectorAll("pre")).filter((p) => p.textContent === "");
    expect(emptyPres.length).toBe(0);
  });

  it("big_json「仍要加载」按需拉取并展示截断提示（默认零 fetch 之外的第二步）", async () => {
    const bigContent = JSON.stringify({ pad: "x".repeat(300 * 1024) });
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("path")) {
          return new HttpResponse(bigContent, { headers: { "content-type": "text/plain" } });
        }
        return HttpResponse.json(
          makeSummary({ files: [{ path: "whitebox/parameter_graph.json", size: 307200, kind: "big_json" }] }),
        );
      }),
    );
    renderAt("/p/ws/scans/scan1/deliverables");
    fireEvent.click(await screen.findByText("parameter_graph.json"));
    // 默认：过大提示 + 「仍要加载」按钮
    await waitFor(() => expect(screen.getByText(/文件过大/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /仍要加载/ })).toBeInTheDocument();
    // 按需加载：内容渲染 + 截断提示（> 256KB 展示上限）
    fireEvent.click(screen.getByRole("button", { name: /仍要加载/ }));
    await waitFor(() => expect(screen.getByText(/仅显示前/)).toBeInTheDocument());
    expect(screen.getByText(/262144/)).toBeInTheDocument();
  });
});

describe("DeliverablesTab i18n", () => {
  afterEach(() => i18n.changeLanguage("zh"));

  it("切英文后聚合标题变英文", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", () => HttpResponse.json(makeSummary())),
    );
    renderAt("/p/ws/scans/scan1/deliverables");
    await screen.findByText(/漏洞聚合/);
    await i18n.changeLanguage("en");
    expect(await screen.findByText(/Vulnerability aggregation/)).toBeInTheDocument();
  });

  it("切英文后空态标题变英文", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", () => HttpResponse.json(makeSummary())),
    );
    renderAt("/p/ws/scans/scan1/deliverables");
    await screen.findByText(/暂无聚合漏洞/);
    await i18n.changeLanguage("en");
    expect(await screen.findByText("No aggregated vulnerabilities")).toBeInTheDocument();
  });

  it("切英文后 injection 类 badge 变英文", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", () =>
        HttpResponse.json(makeSummary({ notes: { injection_has_no_queue: true } })),
      ),
    );
    renderAt("/p/ws/scans/scan1/deliverables");
    await screen.findByText(/injection 类/);
    await i18n.changeLanguage("en");
    expect(await screen.findByText(/injection class/)).toBeInTheDocument();
  });

  it("切英文后产物加载失败变英文", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/deliverables", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    renderAt("/p/ws/scans/scan1/deliverables");
    await screen.findByText(/产物加载失败/);
    await i18n.changeLanguage("en");
    expect(await screen.findByText(/Deliverables load failed/)).toBeInTheDocument();
  });
});
