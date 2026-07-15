import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import i18n from "@/i18n";
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
  // 默认仓库列表为空——repo 相关用例各自 server.use 注入。
  http.get("/api/repos", () => HttpResponse.json([])),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
// jsdom navigator.language 默认 en,LanguageDetector 会把 i18n 切到 en;迁移后断言依赖中文渲染,逐测试钉回 zh。
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
  cleanup();
});
afterAll(() => server.close());

function renderPage(initialPath = "/scan/new") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <ScanNewPage />
    </MemoryRouter>,
  );
}

// 自定义 tab 按钮用 onClick（非 Radix 的 onMouseDown），故用 fireEvent.click。
function clickTab(name: string) {
  fireEvent.click(screen.getByRole("tab", { name }));
}

// Radix Select Trigger 在 jsdom 里走 fireEvent.click 打开下拉（与 WorkspaceListPage
// 一致）。brief 推荐的 mouseDown 在本 jsdom 版本会触发 pointerCapture 未实现错误，
// click 是已验证可复现的姿势——见 task-web-10 报告。
function selectOption(triggerText: RegExp | string, optionName: RegExp | string) {
  const trigger = screen.getByText(triggerText).closest("button")!;
  fireEvent.click(trigger);
  return screen.findByRole("option", { name: optionName }).then((opt) => {
    fireEvent.click(opt);
  });
}

// repo 模式下 selectedRepo 的 Combobox 在「代码源」StepGroup 内。
// StepGroup 内有两个 combobox（sourceKind Select + repo Combobox），取最后一个 = repo。
function selectRepoOption(optionName: RegExp | string) {
  // class 选择器不走 closest 标签名重载（标签名精确返回 HTMLFieldSetElement 等 HTMLElement
  // 子类，class/复合选择器回落 Element），故显式 <HTMLElement> 收窄给 within()。
  const step = screen.getByText("代码源").closest<HTMLElement>(".rounded-lg")!;
  const trigger = within(step).getAllByRole("combobox").at(-1)!;
  fireEvent.click(trigger);
  return screen.findByRole("option", { name: optionName }).then((opt) => {
    fireEvent.click(opt);
  });
}

// Task 10 起：默认 sourceKind=repo，path 模式下需先切到「本地路径」。
// 默认 schema=「选择仓库」fieldset；这里填合法 path + url 让提交可用。
async function fillValidPath() {
  await selectOption(/已下载仓库/, "本地路径");
  fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "/root/code/foo" } });
  fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
}

describe("ScanNewPage", () => {
  it("默认白盒：显示「选择仓库」fieldset，无 reuse 复选框；切黑盒显示 reuse", async () => {
    renderPage();
    // repo kind 默认：有「+ 添加新仓库」按钮 + 「已下载仓库」SelectValue
    expect(screen.getByText("已下载仓库")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /\+ 添加新仓库/ })).toBeInTheDocument();
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
    // 联动页不显示「选择仓库」fieldset
    expect(screen.queryByText(/\+ 添加新仓库/)).toBeNull();
  });

  it("黑盒 --latest 陷阱：reuse 复选框旁有可追溯说明（不勾选=standalone）", () => {
    renderPage();
    clickTab("黑盒");
    expect(screen.getByText(/--latest/)).toBeInTheDocument();
    expect(screen.getByText(/standalone/)).toBeInTheDocument();
  });

  it("workspace 名冲突 + 点提交 → 弹断点续扫 Dialog", async () => {
    renderPage();
    await fillValidPath();
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
    await fillValidPath();
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(expect.stringMatching(/Temporal/i)));
  });

  it("提交 409 → toast 并发扫描超限", async () => {
    server.use(http.post("/api/scan", () => new HttpResponse(null, { status: 409 })));
    const spy = vi.spyOn(toast, "error");
    renderPage();
    await fillValidPath();
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
    await fillValidPath();
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
    await fillValidPath();
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const arg = (spy.mock.calls[0] as string[])[0];
    expect(arg).toContain("yaml 校验失败");
    expect(arg).not.toContain("{");
  });

  it("path 模式显「📁 浏览」trigger", async () => {
    renderPage();
    await selectOption(/已下载仓库/, "本地路径");
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
    await selectOption(/已下载仓库/, "本地路径");
    fireEvent.click(screen.getByRole("button", { name: /📁 浏览/ }));
    // FileSystemPicker Dialog 打开（title 默认"选择代码目录"）
    await waitFor(() => expect(screen.getByText("选择代码目录")).toBeInTheDocument());
    expect(screen.getByText("code")).toBeInTheDocument();
  });

  it("repo 默认未选 → 提交 disabled；选 path 填齐 → enabled", async () => {
    renderPage();
    expect(screen.getByRole("button", { name: /开始扫描/ })).toBeDisabled();
    await fillValidPath();
    expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled();
  });

  it("path 非绝对 → 红字 + 提交 disabled", async () => {
    renderPage();
    await selectOption(/已下载仓库/, "本地路径");
    fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "relative/path" } });
    expect(screen.getByText(/需为绝对路径/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /开始扫描/ })).toBeDisabled();
  });

  it("path 模式：wsName 空 + sourceValue 填 → 显预览名（basename + _YYYYMMDD-HHMMSS）", async () => {
    renderPage();
    await selectOption(/已下载仓库/, "本地路径");
    fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "/root/code/foo" } });
    expect(screen.getByText(/预览名：foo_\d{8}-\d{6}/)).toBeInTheDocument();
  });

  it("path 模式：wsName 填了 → 不显预览", async () => {
    renderPage();
    await selectOption(/已下载仓库/, "本地路径");
    fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "/root/code/foo" } });
    fireEvent.change(screen.getByPlaceholderText(/自动/), { target: { value: "myname" } });
    expect(screen.queryByText(/预览名/)).toBeNull();
  });

  it("续扫 Dialog：取消 → 清空 wsName", async () => {
    renderPage();
    await fillValidPath();
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
    await fillValidPath();
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

  // === 新增：repo 预选 / repo 选择 / not-ready ===

  it("URL ?repo=foo → 预选仓库 foo + buildBody source.kind=repo", async () => {
    let captured: { source?: { kind?: string; value?: string } } | undefined;
    server.use(
      http.get("/api/repos", () =>
        HttpResponse.json([
          { name: "foo", state: "ready", source: { kind: "git", url: "https://gitlab.example/foo.git" } },
        ]),
      ),
      http.post("/api/scan", async ({ request }) => {
        captured = (await request.json()) as { source?: { kind?: string; value?: string } };
        return HttpResponse.json({ workspace: "foo-ws" }, { status: 202 });
      }),
    );
    renderPage("/scan/new?repo=foo");
    // 等 repo 列表加载 + preset 生效（仓库 Combobox 触发器=fieldset 内最后一个 combobox，显示选中短名 foo）
    await waitFor(() =>
      expect(screen.getAllByRole("combobox").at(-1)).toHaveTextContent("foo"),
    );
    // 填 url 提交
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), {
      target: { value: "http://example.com" },
    });
    await waitFor(() => expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(captured).toBeDefined());
    expect(captured!.source?.kind).toBe("repo");
    expect(captured!.source?.value).toBe("foo");
  });

  it("手选 repo 且就绪 → buildBody source.kind=repo", async () => {
    let captured: { source?: { kind?: string; value?: string } } | undefined;
    server.use(
      http.get("/api/repos", () =>
        HttpResponse.json([
          { name: "bar", state: "ready", source: { kind: "git", url: "https://gitlab.example/bar.git" } },
        ]),
      ),
      http.post("/api/scan", async ({ request }) => {
        captured = (await request.json()) as { source?: { kind?: string; value?: string } };
        return HttpResponse.json({ workspace: "bar-ws" }, { status: 202 });
      }),
    );
    renderPage();
    // repo kind 默认；点 selectedRepo SelectTrigger（第 2 个 combobox）→ 选 bar
    await selectRepoOption(/bar/);
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), {
      target: { value: "http://example.com" },
    });
    await waitFor(() => expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(captured).toBeDefined());
    expect(captured!.source?.kind).toBe("repo");
    expect(captured!.source?.value).toBe("bar");
  });

  it("选中的 repo 正在 cloning → 显 CloneProgress（clone 中文案）", async () => {
    server.use(
      http.get("/api/repos", () =>
        HttpResponse.json([
          { name: "wip", state: "cloning", source: { kind: "git", url: "https://gitlab.example/wip.git" } },
        ]),
      ),
      // CloneProgress 走 SSE；返回空流即可（不影响"clone 中"渲染）
      http.get("/api/repos/wip/events", () => new HttpResponse("", { headers: { "Content-Type": "text/event-stream" } })),
    );
    renderPage("/scan/new?repo=wip");
    await waitFor(() =>
      expect(screen.getAllByRole("combobox").at(-1)).toHaveTextContent("wip"),
    );
    // 状态=cloning → CloneProgress 渲染"clone 中"
    await waitFor(() => expect(screen.getByText(/clone 中/)).toBeInTheDocument());
  });

  it("选中的 repo state=failed → 显仓库未就绪提示", async () => {
    server.use(
      http.get("/api/repos", () =>
        HttpResponse.json([
          { name: "broken", state: "failed", source: { kind: "git", url: "https://gitlab.example/broken.git" } },
        ]),
      ),
    );
    renderPage("/scan/new?repo=broken");
    await waitFor(() =>
      expect(screen.getAllByRole("combobox").at(-1)).toHaveTextContent("broken"),
    );
    expect(screen.getByText(/仓库未就绪/)).toBeInTheDocument();
  });

  it("白盒 URL 可选：不填 url + path 填齐 → 可提交", async () => {
    renderPage();
    await selectOption(/已下载仓库/, "本地路径");
    fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "/root/code/foo" } });
    // 白盒扫本地代码，url 仅作黑盒 --latest 匹配锚点 → 不填也能提交
    await waitFor(() => expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled());
  });

  it("黑盒 URL 必填：不填 url → disabled；填齐 → enabled", async () => {
    renderPage();
    clickTab("黑盒");
    await selectOption(/已下载仓库/, "本地路径");
    fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "/root/code/foo" } });
    // 黑盒扫运行中服务 → url 必填，不填时提交 disabled（黑盒按钮文案为"开始渗透"）
    expect(screen.getByRole("button", { name: /开始渗透/ })).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
    await waitFor(() => expect(screen.getByRole("button", { name: /开始渗透/ })).toBeEnabled());
  });
});
