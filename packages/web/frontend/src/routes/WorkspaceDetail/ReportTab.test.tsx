import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
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

describe("ReportTab", () => {
  it("GET /report (text/plain) → 经 MarkdownView 渲染（标题 H1 出现）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/report", () =>
        new HttpResponse(MD, { headers: { "content-type": "text/plain" } }),
      ),
    );
    renderAt("/p/ws/scans/scan1/report");
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1, name: /综合安全评估报告/ })).toBeInTheDocument(),
    );
  });

  it("报告渲染后点「下载 .md」→ downloadTextFile 收 md 全文 + 单报告文件名", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/report", () =>
        new HttpResponse(MD, { headers: { "content-type": "text/plain" } }),
      ),
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

  it("请求失败渲染 ErrorState（role=alert）不永久 loading", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/report", () =>
        HttpResponse.text("not found", { status: 404 }),
      ),
    );
    renderAt("/p/ws/scans/scan1/report");
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    // 守卫：不渲染加载占位 / 空态
    expect(screen.queryByText(/报告尚未生成/)).not.toBeInTheDocument();
  });

  it("空报告（apiGetText 返 \"\"）渲染 Empty 而非加载态", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/report", () =>
        new HttpResponse("", { headers: { "content-type": "text/plain" } }),
      ),
    );
    renderAt("/p/ws/scans/scan1/report");
    await waitFor(() => expect(screen.getByText(/报告尚未生成/)).toBeInTheDocument());
    // 守卫：不渲染 Skeleton 加载态
    expect(document.querySelector(".animate-pulse")).not.toBeInTheDocument();
  });
});

describe("ReportTab i18n", () => {
  afterEach(() => i18n.changeLanguage("zh"));

  it("切英文后空态标题变英文", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId/report", () =>
        new HttpResponse("", { headers: { "content-type": "text/plain" } }),
      ),
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
        new HttpResponse(MD, { headers: { "content-type": "text/plain" } }),
      ),
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
