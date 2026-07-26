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

// P2: 扫描目标 ws 必须从下拉选——选项来自 /workspaces（P1 后端已按当前用户可见性过滤）。
// 默认 ws 列表覆盖 ws1 / ws2 两个，模拟用户已有 ws 的常见态。
const WS_LIST = [
  { name: "ws1", scan_type: "whitebox", status: "completed", created_at: 0 },
  { name: "ws2", scan_type: "blackbox", status: "completed", created_at: 0 },
];

const server = setupServer(
  http.get("/api/workspaces", () => HttpResponse.json(WS_LIST)),
  // P2: repo 已迁到 ws 内——默认空列表，repo 相关用例各自 server.use 注入。
  http.get("/api/workspaces/:ws/repos", () => HttpResponse.json([])),
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
// 一致）。brief 推荐的 mouseDown 在本 jsdom 版本会触发 pointerCapture 未实现错误,
// click 是已验证可复现的姿势——见 task-web-10 报告。
function selectOption(triggerText: RegExp | string, optionName: RegExp | string) {
  const trigger = screen.getByText(triggerText).closest("button")!;
  fireEvent.click(trigger);
  return screen.findByRole("option", { name: optionName }).then((opt) => {
    fireEvent.click(opt);
  });
}

// P2: 选定目标 workspace——下拉 trigger 初始显 placeholder "选择 workspace"。
// 用 exact 字符串匹配（非 regex）——因 hint 文案 "请先选择 workspace（…）" 也含子串 "选择 workspace"，
// regex 会两处命中报错；exact 仅匹配 SelectValue span 的完整文本。
async function selectWorkspace(name: string) {
  await selectOption("选择 workspace", name);
}

// repo 模式下 selectedRepo 的 Combobox 在「代码源」StepGroup 内。
// StepGroup 内有两个 combobox（sourceKind Select + repo Combobox），取最后一个 = repo。
// P2: ws Select 在另一个 StepGroup（"目标信息"/"工作区"）——全文档扫描 .at(-1) 会拿到 ws
// Select，故必须用 within(scope) 把搜索限定在「代码源」组内。
function repoCombobox() {
  const step = screen.getByText("代码源").closest<HTMLElement>(".rounded-lg")!;
  return within(step).getAllByRole("combobox").at(-1)!;
}

function selectRepoOption(optionName: RegExp | string) {
  const trigger = repoCombobox();
  fireEvent.click(trigger);
  return screen.findByRole("option", { name: optionName }).then((opt) => {
    fireEvent.click(opt);
  });
}

// 默认 sourceKind=repo；这里：先选 ws → 切 path → 填齐 path + url，让提交可用。
// P2 起：所有提交类用例必须先选 ws（提交 body workspace_name 必填）。
async function fillValidPath() {
  await selectWorkspace("ws1");
  await selectOption(/已下载仓库/, "本地路径");
  fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "/root/code/foo" } });
  fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
}

describe("ScanNewPage", () => {
  it("默认白盒：未选 ws 显提示，选 ws 后显仓库选择器 + 添加按钮；切黑盒显 reuse", async () => {
    renderPage();
    // ws 未选 → 显「请先选择 workspace」提示，无「+ 添加新仓库」按钮
    expect(screen.getByText(/请先选择 workspace/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /\+ 添加新仓库/ })).toBeNull();
    // 黑盒专属复选框默认不出现
    expect(screen.queryByText(/复用最新白盒/)).toBeNull();
    // 选 ws1 → 提示消失，仓库 picker + 添加按钮出现
    await selectWorkspace("ws1");
    await waitFor(() => expect(screen.queryByText(/请先选择 workspace/)).toBeNull());
    expect(screen.getByRole("button", { name: /\+ 添加新仓库/ })).toBeInTheDocument();
    // 切到黑盒 → reuse 复选框出现（--latest 软默认陷阱标注）
    clickTab("黑盒");
    expect(screen.getByText(/复用最新白盒/)).toBeInTheDocument();
  });

  // === P2 新增：ws 下拉渲染 + 选 ws 驱动 listRepos(<ws>) ===
  it("ws 下拉含 /workspaces 选项；选 ws 驱动 listRepos(<ws>)", async () => {
    const repoCalls: string[] = [];
    server.use(
      http.get("/api/workspaces/:ws/repos", ({ params }) => {
        repoCalls.push(params.ws as string);
        return HttpResponse.json([]);
      }),
    );
    renderPage();
    // 初始：未选 ws → 未发起 listRepos
    expect(repoCalls).toEqual([]);
    // 展开下拉 → 显 ws1 / ws2 两个选项（exact 字符串匹配避免与 hint 冲突）
    fireEvent.click(screen.getByText("选择 workspace").closest("button")!);
    expect(await screen.findByRole("option", { name: "ws1" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "ws2" })).toBeInTheDocument();
    // 选 ws1 → 触发 listRepos("ws1")
    fireEvent.click(screen.getByRole("option", { name: "ws1" }));
    await waitFor(() => expect(repoCalls).toContain("ws1"));
    // 选 ws2 → 再触发 listRepos("ws2")（refetch on workspace change）
    // trigger 现在显示选中值 "ws1"，重新打开选 ws2
    fireEvent.click(screen.getByText("ws1").closest("button")!);
    fireEvent.click(await screen.findByRole("option", { name: "ws2" }));
    await waitFor(() => expect(repoCalls).toContain("ws2"));
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

  it("未选 ws + repo 默认未选 → 提交 disabled；选 ws + path 填齐 → enabled", async () => {
    renderPage();
    // 默认：未选 ws → disabled
    expect(screen.getByRole("button", { name: /开始扫描/ })).toBeDisabled();
    await fillValidPath();
    expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled();
  });

  it("path 非绝对 → 红字 + 提交 disabled", async () => {
    renderPage();
    await selectWorkspace("ws1");
    await selectOption(/已下载仓库/, "本地路径");
    fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "relative/path" } });
    expect(screen.getByText(/需为绝对路径/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /开始扫描/ })).toBeDisabled();
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

  // === repo 预选 / repo 选择 / not-ready（P2：repo 路径已迁到 /workspaces/<ws>/repos） ===

  it("URL ?repo=foo → 选 ws 后预选 foo 显出 + buildBody workspace_name=选中 ws", async () => {
    let captured: { source?: { kind?: string; value?: string }; workspace_name?: string } | undefined;
    server.use(
      http.get("/api/workspaces/:ws/repos", () =>
        HttpResponse.json([
          { name: "foo", state: "ready", source: { kind: "git", url: "https://gitlab.example/foo.git" } },
        ]),
      ),
      http.post("/api/scan", async ({ request }) => {
        captured = (await request.json()) as { source?: { kind?: string; value?: string }; workspace_name?: string };
        return HttpResponse.json({ workspace: "ws1" }, { status: 202 });
      }),
    );
    renderPage("/scan/new?repo=foo");
    // 选 ws1 → listRepos(ws1) 拉到 foo → 仓库 combobox 显选中短名 foo
    await selectWorkspace("ws1");
    await waitFor(() =>
      expect(repoCombobox()).toHaveTextContent("foo"),
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
    expect(captured!.workspace_name).toBe("ws1");
  });

  it("手选 repo 且就绪 → buildBody source.kind=repo", async () => {
    let captured: { source?: { kind?: string; value?: string }; workspace_name?: string } | undefined;
    server.use(
      http.get("/api/workspaces/:ws/repos", () =>
        HttpResponse.json([
          { name: "bar", state: "ready", source: { kind: "git", url: "https://gitlab.example/bar.git" } },
        ]),
      ),
      http.post("/api/scan", async ({ request }) => {
        captured = (await request.json()) as { source?: { kind?: string; value?: string }; workspace_name?: string };
        return HttpResponse.json({ workspace: "bar-ws" }, { status: 202 });
      }),
    );
    renderPage();
    // 先选 ws1 → repo picker 出现 → 手选 bar
    await selectWorkspace("ws1");
    await waitFor(() => screen.getByRole("button", { name: /\+ 添加新仓库/ }));
    await selectRepoOption(/bar/);
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), {
      target: { value: "http://example.com" },
    });
    await waitFor(() => expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(captured).toBeDefined());
    expect(captured!.source?.kind).toBe("repo");
    expect(captured!.source?.value).toBe("bar");
    expect(captured!.workspace_name).toBe("ws1");
  });

  it("选中的 repo 正在 cloning → 显 CloneProgress（clone 中文案）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/repos", () =>
        HttpResponse.json([
          { name: "wip", state: "cloning", source: { kind: "git", url: "https://gitlab.example/wip.git" } },
        ]),
      ),
      // CloneProgress 走 SSE；返回空流即可（不影响"clone 中"渲染）
      http.get("/api/workspaces/:ws/repos/wip/events", () => new HttpResponse("", { headers: { "Content-Type": "text/event-stream" } })),
    );
    renderPage("/scan/new?repo=wip");
    await selectWorkspace("ws1");
    await waitFor(() =>
      expect(repoCombobox()).toHaveTextContent("wip"),
    );
    // 状态=cloning → CloneProgress 渲染"clone 中"
    await waitFor(() => expect(screen.getByText(/clone 中/)).toBeInTheDocument());
  });

  it("选中的 repo state=failed → 显仓库未就绪提示", async () => {
    server.use(
      http.get("/api/workspaces/:ws/repos", () =>
        HttpResponse.json([
          { name: "broken", state: "failed", source: { kind: "git", url: "https://gitlab.example/broken.git" } },
        ]),
      ),
    );
    renderPage("/scan/new?repo=broken");
    await selectWorkspace("ws1");
    await waitFor(() =>
      expect(repoCombobox()).toHaveTextContent("broken"),
    );
    expect(screen.getByText(/仓库未就绪/)).toBeInTheDocument();
  });

  it("白盒 URL 可选：不填 url + path 填齐 + 选 ws → 可提交", async () => {
    renderPage();
    await selectWorkspace("ws1");
    await selectOption(/已下载仓库/, "本地路径");
    fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "/root/code/foo" } });
    // 白盒扫本地代码，url 仅作黑盒 --latest 匹配锚点 → 不填也能提交
    await waitFor(() => expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled());
  });

  it("黑盒 URL 必填：不填 url → disabled；填齐 → enabled", async () => {
    renderPage();
    clickTab("黑盒");
    await selectWorkspace("ws1");
    await selectOption(/已下载仓库/, "本地路径");
    fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "/root/code/foo" } });
    // 黑盒扫运行中服务 → url 必填，不填时提交 disabled（黑盒按钮文案为"开始渗透"）
    expect(screen.getByRole("button", { name: /开始渗透/ })).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
    await waitFor(() => expect(screen.getByRole("button", { name: /开始渗透/ })).toBeEnabled());
  });
});

describe("ScanNewPage 配色 · coral 收窄到点缀（对齐全站克制基调）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("侧栏标题中性化（muted），不铺 coral 块级底色", () => {
    renderPage();
    const cap = screen.getByText("审计范围");
    expect(cap.className).toMatch(/text-muted-foreground/);
    expect(cap.className).not.toMatch(/text-primary/);
  });

  it("侧栏信息卡中性浮起（bg-secondary + border-border），coral 不铺块级底色", () => {
    renderPage();
    const card = screen.getByText("分析方式").closest(".rounded-lg");
    expect(card?.className).toMatch(/bg-secondary/);
    expect(card?.className).toMatch(/border-border/);
    // coral(primary) 仅作点缀：不铺侧栏卡块级底色/描边
    expect(card?.className).not.toMatch(/bg-primary/);
    expect(card?.className).not.toMatch(/border-primary/);
  });

  it("底部操作栏用 bg-card（去 secondary 灰堆叠）", () => {
    renderPage();
    const footer = screen.getByRole("button", { name: /开始扫描/ }).parentElement;
    expect(footer?.className).not.toMatch(/bg-secondary/);
    expect(footer?.className).toMatch(/bg-card/);
  });
});
