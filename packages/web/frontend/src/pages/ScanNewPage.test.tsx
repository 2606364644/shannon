import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { ScanNewPage } from "./ScanNewPage";

// Monaco 在测试里替换成 textarea（data-testid="monaco"），同 YamlEditor.test 模式
vi.mock("@monaco-editor/react", () => ({
  default: ({ value, onChange }: { value: string; onChange?: (v: string) => void }) => (
    <textarea data-testid="monaco" value={value} onChange={(e) => onChange?.(e.target.value)} />
  ),
}));

const server = setupServer(
  http.get("/api/workspaces", () =>
    HttpResponse.json([
      { name: "existing-ws", scan_type: "whitebox", status: "completed", created_at: 0 },
    ]),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
  cleanup();
});
afterAll(() => server.close());

function renderPage() {
  return render(
    <MemoryRouter>
      <ScanNewPage />
    </MemoryRouter>,
  );
}

// Radix Tabs 的 TabsTrigger 在 onMouseDown（button===0）里调 onValueChange 激活，
// 而 fireEvent.click 在 jsdom 里不发 mousedown 事件——故切 tab 用 mouseDown
// 模拟真实激活手势（不是 click）。
function clickTab(name: string) {
  fireEvent.mouseDown(screen.getByRole("tab", { name }));
}

// Task 5 起，提交按钮在必填空 / 格式错时 disabled；测试提交场景前先填齐合法值。
function fillValid() {
  fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "/root/code/foo" } });
  fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
}

describe("ScanNewPage", () => {
  it("默认白盒：显示代码来源，无 reuse 复选框；切黑盒显示 reuse", () => {
    renderPage();
    // 用 exact 精确命中 <legend>代码来源</legend>；即时校验空值错误文"代码来源不能为空"也匹配 /代码来源/，需避开。
    expect(screen.getByText("代码来源", { exact: true })).toBeInTheDocument();
    // 黑盒专属复选框默认不出现
    expect(screen.queryByText(/复用最新白盒/)).toBeNull();
    // 切到黑盒 → reuse 复选框出现（--latest 软默认陷阱标注）
    clickTab("黑盒");
    expect(screen.getByText(/复用最新白盒/)).toBeInTheDocument();
  });

  it("切联动：显示 yaml 编辑器，隐藏白盒字段", () => {
    renderPage();
    clickTab("联动");
    expect(screen.getByTestId("monaco")).toBeInTheDocument();
    // 联动页不显示白盒/黑盒的代码来源字段
    expect(screen.queryByText(/代码来源/)).toBeNull();
  });

  it("黑盒 --latest 陷阱：reuse 复选框旁有可追溯说明（不勾选=standalone）", () => {
    renderPage();
    clickTab("黑盒");
    expect(screen.getByText(/--latest/)).toBeInTheDocument();
    expect(screen.getByText(/standalone/)).toBeInTheDocument();
  });

  it("workspace 名冲突 + 点提交 → 弹断点续扫 Dialog", async () => {
    renderPage();
    fillValid();
    fireEvent.change(screen.getByPlaceholderText(/自动/), {
      target: { value: "existing-ws" },
    });
    // 等 debounce 冲突检测完（loadingConflict 期间 button disabled）
    await waitFor(() => expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    const dlg = await waitFor(() => screen.getByRole("dialog"));
    expect(dlg.textContent).toMatch(/断点续扫/);
    expect(dlg.textContent).toContain("existing-ws");
  });

  it("提交 400 → toast 提示 Temporal 未就绪", async () => {
    server.use(http.post("/api/scan", () => new HttpResponse(null, { status: 400 })));
    const spy = vi.spyOn(toast, "error");
    renderPage();
    fillValid();
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(expect.stringMatching(/Temporal/i)));
  });

  it("提交 409 → toast 并发扫描超限", async () => {
    server.use(http.post("/api/scan", () => new HttpResponse(null, { status: 409 })));
    const spy = vi.spyOn(toast, "error");
    renderPage();
    fillValid();
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(expect.stringMatching(/并发扫描超限/)));
  });

  it("提交 422 → toast yaml 校验失败（友好消息，不含原始 JSON）", async () => {
    server.use(
      http.post("/api/scan", () =>
        HttpResponse.json(
          { detail: [{ loc: ["body", "config_yaml"], msg: "repo url required", type: "value_error" }] },
          { status: 422 },
        ),
      ),
    );
    const spy = vi.spyOn(toast, "error");
    renderPage();
    fillValid();
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const arg = (spy.mock.calls[0] as string[])[0];
    expect(arg).toContain("yaml 校验失败");
    expect(arg).toContain("repo url required");
    expect(arg).not.toContain("value_error");
  });

  it("422 无 detail → toast 回退纯标签", async () => {
    server.use(
      http.post("/api/scan", () => HttpResponse.json({ something: "else" }, { status: 422 })),
    );
    const spy = vi.spyOn(toast, "error");
    renderPage();
    fillValid();
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const arg = (spy.mock.calls[0] as string[])[0];
    expect(arg).toContain("yaml 校验失败");
    expect(arg).not.toContain("{");
  });

  it("path 时显「📁 浏览」trigger", () => {
    renderPage();
    expect(screen.getByRole("button", { name: /📁 浏览/ })).toBeInTheDocument();
  });

  it("点「📁 浏览」→ 打开文件浏览器 → 显目录 entry", async () => {
    server.use(
      http.get("/api/fs/browse", () =>
        HttpResponse.json({
          path: "/",
          parent: null,
          entries: [{ name: "code", type: "dir" }],
        }),
      ),
    );
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /📁 浏览/ }));
    // FileSystemPicker Dialog 打开（title 默认"选择代码目录"）
    await waitFor(() => expect(screen.getByText("选择代码目录")).toBeInTheDocument());
    expect(screen.getByText("code")).toBeInTheDocument();
  });

  it("必填空 → 提交 disabled；填齐 → enabled", () => {
    renderPage();
    expect(screen.getByRole("button", { name: /开始扫描/ })).toBeDisabled();
    fillValid();
    expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled();
  });

  it("path 非绝对 → 红字 + 提交 disabled", () => {
    renderPage();
    fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "relative/path" } });
    expect(screen.getByText(/需为绝对路径/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /开始扫描/ })).toBeDisabled();
  });

  it("wsName 空 + sourceValue 填 → 显预览名（basename + _YYYYMMDD-HHMMSS）", () => {
    renderPage();
    fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "/root/code/foo" } });
    expect(screen.getByText(/预览名：foo_\d{8}-\d{6}/)).toBeInTheDocument();
  });

  it("wsName 填了 → 不显预览", () => {
    renderPage();
    fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "/root/code/foo" } });
    fireEvent.change(screen.getByPlaceholderText(/自动/), { target: { value: "myname" } });
    expect(screen.queryByText(/预览名/)).toBeNull();
  });

  it("续扫 Dialog：取消 → 清空 wsName", async () => {
    renderPage();
    fillValid();
    fireEvent.change(screen.getByPlaceholderText(/自动/), { target: { value: "existing-ws" } });
    await waitFor(() => expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => screen.getByRole("dialog"));
    fireEvent.click(screen.getByRole("button", { name: /取消/ }));
    await waitFor(() =>
      expect((screen.getByPlaceholderText(/自动/) as HTMLInputElement).value).toBe(""),
    );
  });

  it("续扫 Dialog：确认续扫 → 提交 /scan 202（无 toast.error）", async () => {
    server.use(
      http.post("/api/scan", () => HttpResponse.json({ workspace: "existing-ws" }, { status: 202 })),
    );
    const spy = vi.spyOn(toast, "error");
    renderPage();
    fillValid();
    fireEvent.change(screen.getByPlaceholderText(/自动/), { target: { value: "existing-ws" } });
    await waitFor(() => expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => screen.getByRole("dialog"));
    fireEvent.click(screen.getByRole("button", { name: /确认续扫/ }));
    await waitFor(() => expect(spy).not.toHaveBeenCalled());
  });

  it("扫描页无 events.css 遗留 class（浅色主题不断裂）", () => {
    const { container } = renderPage();
    expect(container.querySelector(".page.scan-page")).toBeNull();
    expect(container.querySelector(".submit-btn")).toBeNull();
    expect(container.querySelector(".trace")).toBeNull();
    expect(container.querySelector(".git-extra")).toBeNull();
    expect(container.querySelector(".yaml-editor")).toBeNull();
    expect(container.querySelector(".ev-warn")).toBeNull();
  });
});
