import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { LogsTab } from "./LogsTab";

// LogsTab 用 apiGet<{content:string}> 取文件（JSON 解析），故 ?file= 响应须返 {content}。

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/p/:workspace/logs" element={<LogsTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** 生成 N 行极短文本（确保按行数而非字符数跨阈值）。
 * 每行约 8 字符（"line-NNN"），6000 行总字符数 ~52k，
 * 远低于旧 CHAR 阈值 100k → 旧实现不会虚拟化，新（行）实现会。 */
function makeShortLines(n: number): string {
  const lines: string[] = [];
  for (let i = 0; i < n; i++) lines.push(`line-${i}`);
  return lines.join("\n");
}

describe("LogsTab", () => {
  it("日志文件列表渲染 + 点击 .log 文件加载内容", async () => {
    server.use(
      http.get("/api/workspaces/:ws/logs", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("file")) {
          return HttpResponse.json({ content: JSON.stringify({ ts: "t1", type: "AgentEvent", message: "hello" }) });
        }
        return HttpResponse.json({ files: ["workflow.log", "recon.log"] });
      }),
    );
    renderAt("/p/ws/logs");
    await waitFor(() => expect(screen.getByText("recon.log")).toBeInTheDocument());
    fireEvent.click(screen.getByText("recon.log"));
    await waitFor(() => expect(screen.getByText(/hello/)).toBeInTheDocument());
  });

  it(".log 行数跨阈值 → 虚拟滚动（FixedSizeList 挂载 + 大文件提示）", async () => {
    // 6000 行短行：按行计数跨阈值（5000），按字符数远低于旧 100k 阈值。
    // 旧实现（按字符 100k）不会虚拟化 → 此测试在旧实现下 RED。
    const manyLines = makeShortLines(6000);
    server.use(
      http.get("/api/workspaces/:ws/logs", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("file")) {
          return HttpResponse.json({ content: manyLines });
        }
        return HttpResponse.json({ files: ["recon.log"] });
      }),
    );
    const { container } = renderAt("/p/ws/logs");
    await waitFor(() => expect(screen.getByText("recon.log")).toBeInTheDocument());
    fireEvent.click(screen.getByText("recon.log"));
    // 虚拟滚动提示出现
    await waitFor(() => expect(screen.getByText(/虚拟滚动/)).toBeInTheDocument());
    // 结构断言：虚拟化只渲染可见窗口，远小于总行数 6000
    const renderedRows = container.querySelectorAll(".log-row, .trace");
    expect(renderedRows.length).toBeLessThan(100);
  });

  it(".log 行数未跨阈值 → 不虚拟滚动（全部行直接渲染）", async () => {
    // 100 行短行：远低于行阈值，应直接渲染全部。
    const fewLines = makeShortLines(100);
    server.use(
      http.get("/api/workspaces/:ws/logs", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("file")) {
          return HttpResponse.json({ content: fewLines });
        }
        return HttpResponse.json({ files: ["recon.log"] });
      }),
    );
    const { container } = renderAt("/p/ws/logs");
    await waitFor(() => expect(screen.getByText("recon.log")).toBeInTheDocument());
    fireEvent.click(screen.getByText("recon.log"));
    await waitFor(() => expect(screen.getByText(/line-0/)).toBeInTheDocument());
    // 不虚拟滚动：无『虚拟滚动』提示
    expect(screen.queryByText(/虚拟滚动/)).not.toBeInTheDocument();
    // 全部 100 行渲染（断言非虚拟化）：非 JSON 行渲染为 .trace
    const rows = container.querySelectorAll(".log-row, .trace");
    expect(rows.length).toBe(100);
  });

  it("非 .log 文件（如 .txt）走 pre 原样渲染", async () => {
    server.use(
      http.get("/api/workspaces/:ws/logs", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("file")) {
          return HttpResponse.json({ content: "plain text content" });
        }
        return HttpResponse.json({ files: ["notes.txt"] });
      }),
    );
    renderAt("/p/ws/logs");
    await waitFor(() => expect(screen.getByText("notes.txt")).toBeInTheDocument());
    fireEvent.click(screen.getByText("notes.txt"));
    await waitFor(() => expect(screen.getByText(/plain text content/)).toBeInTheDocument());
  });

  it("文件列表项是 button（键盘可达）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/logs", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.has("file")) {
          return HttpResponse.json({ content: "log content" });
        }
        return HttpResponse.json({ files: ["workflow.log", "recon.log"] });
      }),
    );
    renderAt("/p/ws/logs");
    await waitFor(() => expect(screen.getByText("recon.log")).toBeInTheDocument());
    // 文件名以 .log 结尾的 button 存在（键盘可达 + aria-current 可用）
    const fileButtons = screen.getAllByRole("button").filter((b) => /\.log$/.test(b.textContent ?? ""));
    expect(fileButtons.length).toBeGreaterThan(0);
    // 选中的文件 button 带 aria-current（选中态可达性）
    fireEvent.click(fileButtons[0]);
    await waitFor(() => expect(fileButtons[0]).toHaveAttribute("aria-current", "true"));
  });

  it("fetch 文件列表失败渲染 ErrorState（role=alert）不永久 loading", async () => {
    server.use(
      http.get("/api/workspaces/:ws/logs", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    renderAt("/p/ws/logs");
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});
