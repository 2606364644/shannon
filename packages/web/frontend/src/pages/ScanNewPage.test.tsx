import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SWRConfig } from "swr";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import i18n from "@/i18n";
import { ScanNewPage, buildBody, buildAuthPayload, validateAuth, presetToAuthState, presetToHostState, type AuthFormState, type FormState, type RerunPreset } from "./ScanNewPage";

// 空态提示按 role 切文案 → useAuth 可控（同 DashboardPage.test 模式）。
const { mockUseAuth } = vi.hoisted(() => ({ mockUseAuth: vi.fn() }));
vi.mock("@/auth/AuthContext", () => ({ useAuth: () => mockUseAuth() }));

// P2: 扫描目标 ws 必须从下拉选——选项来自 /workspaces（P1 后端已按当前用户可见性过滤）。
// 默认 ws 列表覆盖 ws1 / ws2 两个，模拟用户已有 ws 的常见态。
const WS_LIST = [
  { name: "ws1", scan_type: "whitebox", status: "completed", created_at: 0 },
  { name: "ws2", scan_type: "blackbox", status: "completed", created_at: 0 },
];

const userUser = { id: 1, username: "alice", role: "user", must_change_password: false };
const userAdmin = { id: 2, username: "root", role: "admin", must_change_password: false };

const server = setupServer(
  http.get("/api/workspaces", () => HttpResponse.json(WS_LIST)),
  // P2: repo 已迁到 ws 内——默认空列表，repo 相关用例各自 server.use 注入。
  http.get("/api/workspaces/:ws/repos", () => HttpResponse.json([])),
  // 黑盒复用候选：默认该 ws 无 whitebox 扫描（驱动智能默认退到 repo 模式）。
  http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([])),
  // 认证展开默认 source=profile（2026-08-14）→ BottomProfileBlock mount 即拉 auth-profiles；
  // 默认空列表，需具体档案的用例各自 server.use 注入。
  http.get("/api/workspaces/:ws/auth-profiles", () => HttpResponse.json([])),
  // 刷新恢复（2026-09-04）：选 ws 后 mount 即查最近一条拓扑分析——默认 404=从未发起，
  // 需恢复场景的用例各自 server.use 注入。
  http.get("/api/workspaces/:ws/correlation-topology/analyses/latest", () =>
    new HttpResponse(null, { status: 404 })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
// jsdom navigator.language 默认 en,LanguageDetector 会把 i18n 切到 en;迁移后断言依赖中文渲染,逐测试钉回 zh。
// 默认普通用户（空态提示按 role 切文案；现有 ws/repo 用例不依赖 role，给默认 user 无副作用）。
beforeEach(() => {
  i18n.changeLanguage("zh");
  mockUseAuth.mockReturnValue({ user: userUser });
});
afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
  cleanup();
});
afterAll(() => server.close());

function renderPage(initialPath = "/scan/new", state?: unknown) {
  return render(
    <MemoryRouter initialEntries={[state ? { pathname: initialPath, state } : initialPath]}>
      <ScanNewPage />
    </MemoryRouter>,
  );
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

// RepoCombobox 在某 StepGroup 内：白盒 Step2="代码源"。
// ws Select 在另一个 StepGroup（"工作区"）——按 step 标题 scope 避开它。
function repoComboboxIn(stepTitle: string) {
  const step = screen.getByText(stepTitle).closest<HTMLElement>(".rounded-lg")!;
  return within(step).getAllByRole("combobox").at(-1)!;
}

function selectRepoOption(stepTitle: string, optionName: RegExp | string) {
  const trigger = repoComboboxIn(stepTitle);
  fireEvent.click(trigger);
  return screen.findByRole("option", { name: optionName }).then((opt) => {
    fireEvent.click(opt);
  });
}

// 入口已收窄为 repo-only + 白盒去动态（无 URL 输入）：白盒提交类用例统一注入 ready 仓库 + 选 ws + 选 repo。
async function fillValidRepo() {
  server.use(
    http.get("/api/workspaces/:ws/repos", () =>
      HttpResponse.json([
        { name: "foo", state: "ready", source: { kind: "git", url: "https://gitlab.example/foo.git" } },
      ]),
    ),
  );
  await selectWorkspace("ws1");
  await waitFor(() => screen.getByRole("button", { name: /\+ 添加新仓库/ }));
  await selectRepoOption("代码源", /foo/);
}

describe("ScanNewPage", () => {
  it("默认白盒：未选 ws 显提示，选 ws 后显仓库选择器 + 添加按钮", async () => {
    renderPage();
    // ws 未选 → 显「请先选择 workspace」提示，无「+ 添加新仓库」按钮
    expect(screen.getByText(/请先选择 workspace/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /\+ 添加新仓库/ })).toBeNull();
    // 选 ws1 → 提示消失，仓库 picker + 添加按钮出现
    await selectWorkspace("ws1");
    await waitFor(() => expect(screen.queryByText(/请先选择 workspace/)).toBeNull());
    expect(screen.getByRole("button", { name: /\+ 添加新仓库/ })).toBeInTheDocument();
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
    fireEvent.click(screen.getByText("ws1").closest("button")!);
    fireEvent.click(await screen.findByRole("option", { name: "ws2" }));
    await waitFor(() => expect(repoCalls).toContain("ws2"));
  });

  it("提交 400 → toast 提示 Temporal 未就绪", async () => {
    server.use(http.post("/api/scan", () => new HttpResponse(null, { status: 400 })));
    const spy = vi.spyOn(toast, "error");
    renderPage();
    await fillValidRepo();
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(expect.stringMatching(/Temporal/i)));
  });

  it("提交 409 → toast 并发扫描超限", async () => {
    server.use(http.post("/api/scan", () => new HttpResponse(null, { status: 409 })));
    const spy = vi.spyOn(toast, "error");
    renderPage();
    await fillValidRepo();
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(expect.stringMatching(/并发扫描超限/)));
  });

  it("提交 422 → toast yaml 校验失败（友好消息，不含原始 JSON）", async () => {
    server.use(
      http.post("/api/scan", () =>
        HttpResponse.json(
          { detail: [{ loc: ["body", "config_content"], msg: "repo url required", type: "value_error" }] },
          { status: 422 },
        ),
      ),
    );
    const spy = vi.spyOn(toast, "error");
    renderPage();
    await fillValidRepo();
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
    await fillValidRepo();
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const arg = (spy.mock.calls[0] as string[])[0];
    expect(arg).toContain("yaml 校验失败");
    expect(arg).not.toContain("{");
  });

  // 2026-08-27 事故：仓库 pull 失败 → 后端 ValueError("仓库未就绪（state=failed）…") →
  // scan API 422 + string detail。旧 renderError 不认 string → 兜底「yaml 校验失败」，
  // 用户看着「yaml 校验失败」完全无从排查（白盒扫描根本没有 yaml）。string detail 是
  // 后端 ValueError 族的友好中文原文，必须直接透传展示。
  it("422 + string detail（后端 ValueError 原文，如仓库未就绪）→ toast 显示原文，不误报 yaml 校验失败", async () => {
    server.use(
      http.post("/api/scan", () =>
        HttpResponse.json(
          { detail: "仓库未就绪（state=failed），请先在 ws 内完成 clone" },
          { status: 422 },
        ),
      ),
    );
    const spy = vi.spyOn(toast, "error");
    renderPage();
    await fillValidRepo();
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const arg = (spy.mock.calls[0] as string[])[0];
    expect(arg).toContain("仓库未就绪");
    expect(arg).not.toContain("yaml 校验失败");
  });

  it("提交 422 provider_incomplete → toast 提示工作区需配置 LLM 凭据（非 yaml 校验失败）", async () => {
    server.use(
      http.post("/api/scan", () =>
        HttpResponse.json(
          { detail: { code: "provider_incomplete", missing: ["SUPERNOVA_OPENAI_API_KEY"] } },
          { status: 422 },
        ),
      ),
    );
    const spy = vi.spyOn(toast, "error");
    renderPage();
    await fillValidRepo();
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const arg = (spy.mock.calls[0] as string[])[0];
    expect(arg).toContain("工作区");
    expect(arg).not.toContain("yaml 校验失败");
  });

  it("未选 ws + repo 默认未选 → 提交 disabled；选 ws + repo → enabled", async () => {
    renderPage();
    // 默认：未选 ws → disabled
    expect(screen.getByRole("button", { name: /开始扫描/ })).toBeDisabled();
    await fillValidRepo();
    expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled();
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

  it("URL ?repo=foo → 选 ws 后预选 foo 显出 + buildBody workspace=选中 ws", async () => {
    let captured: { source?: { kind?: string; value?: string }; workspace?: string } | undefined;
    server.use(
      http.get("/api/workspaces/:ws/repos", () =>
        HttpResponse.json([
          { name: "foo", state: "ready", source: { kind: "git", url: "https://gitlab.example/foo.git" } },
        ]),
      ),
      http.post("/api/scan", async ({ request }) => {
        captured = (await request.json()) as { source?: { kind?: string; value?: string }; workspace?: string };
        return HttpResponse.json({ workspace: "ws1" }, { status: 202 });
      }),
    );
    renderPage("/scan/new?repo=foo");
    // 选 ws1 → listRepos(ws1) 拉到 foo → 仓库 combobox 显选中短名 foo
    await selectWorkspace("ws1");
    await waitFor(() =>
      expect(repoComboboxIn("代码源")).toHaveTextContent("foo"),
    );
    // 白盒去动态（无 URL 输入）→ 选 ws + 预选 repo 即 enabled，直接提交
    await waitFor(() => expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(captured).toBeDefined());
    expect(captured!.source?.kind).toBe("repo");
    expect(captured!.source?.value).toBe("foo");
    expect(captured!.workspace).toBe("ws1");
  });

  it("手选 repo 且就绪 → buildBody source.kind=repo", async () => {
    let captured: { source?: { kind?: string; value?: string }; workspace?: string } | undefined;
    server.use(
      http.get("/api/workspaces/:ws/repos", () =>
        HttpResponse.json([
          { name: "bar", state: "ready", source: { kind: "git", url: "https://gitlab.example/bar.git" } },
        ]),
      ),
      http.post("/api/scan", async ({ request }) => {
        captured = (await request.json()) as { source?: { kind?: string; value?: string }; workspace?: string };
        return HttpResponse.json({ workspace: "bar-ws" }, { status: 202 });
      }),
    );
    renderPage();
    // 先选 ws1 → repo picker 出现 → 手选 bar
    await selectWorkspace("ws1");
    await waitFor(() => screen.getByRole("button", { name: /\+ 添加新仓库/ }));
    await selectRepoOption("代码源", /bar/);
    // 白盒去动态（无 URL 输入）→ 选 ws + repo 即 enabled
    await waitFor(() => expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(captured).toBeDefined());
    expect(captured!.source?.kind).toBe("repo");
    expect(captured!.source?.value).toBe("bar");
    expect(captured!.workspace).toBe("ws1");
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
      expect(repoComboboxIn("代码源")).toHaveTextContent("wip"),
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
      expect(repoComboboxIn("代码源")).toHaveTextContent("broken"),
    );
    expect(screen.getByText(/仓库未就绪/)).toBeInTheDocument();
  });

  it("白盒去动态无 URL 输入：选 repo + 选 ws → 可提交（无需目标地址）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/repos", () =>
        HttpResponse.json([
          { name: "foo", state: "ready", source: { kind: "git", url: "https://gitlab.example/foo.git" } },
        ]),
      ),
    );
    renderPage();
    await selectWorkspace("ws1");
    await waitFor(() => screen.getByRole("button", { name: /\+ 添加新仓库/ }));
    await selectRepoOption("代码源", /foo/);
    // 白盒已去动态（recon 固定静态）→ 无 URL 输入框，选 ws + repo 即可提交
    await waitFor(() => expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled());
  });

  // === 无可用工作区空态提示（普通用户提示联系管理员 / admin 提示新建工作区） ===
  it("普通用户无 ws → 显「联系管理员」提示，不显 admin 文案；下拉显空态项", async () => {
    server.use(http.get("/api/workspaces", () => HttpResponse.json([])));
    renderPage();
    // 下方提示行（普通用户文案）
    expect(await screen.findByText(/联系管理员/)).toBeInTheDocument();
    expect(screen.queryByText(/新建一个工作区/)).toBeNull();
    // 下拉内 disabled 空态项
    fireEvent.click(screen.getByText("选择 workspace").closest("button")!);
    expect(await screen.findByRole("option", { name: /暂无可用的工作区/ })).toBeInTheDocument();
  });

  it("admin 无 ws → 显「新建工作区」提示", async () => {
    server.use(http.get("/api/workspaces", () => HttpResponse.json([])));
    mockUseAuth.mockReturnValue({ user: userAdmin });
    renderPage();
    expect(await screen.findByText(/新建一个工作区/)).toBeInTheDocument();
    expect(screen.queryByText(/联系管理员/)).toBeNull();
  });

  it("有 ws → 不显空态提示", async () => {
    renderPage();
    // 默认 WS_LIST 非空：展开下拉能看到 ws1（确认 wsList 加载完）→ 不应有任何空态提示
    fireEvent.click(screen.getByText("选择 workspace").closest("button")!);
    expect(await screen.findByRole("option", { name: "ws1" })).toBeInTheDocument();
    expect(screen.queryByText(/联系管理员/)).toBeNull();
    expect(screen.queryByText(/新建一个工作区/)).toBeNull();
  });
});

// === 重跑预填：ScanList.onRerun 经 location.state 传入原扫描配置 ===
describe("ScanNewPage 重跑预填（location.state）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("白盒：state -> tab 白盒 + ws 选中 + repo 选中", async () => {
    server.use(
      http.get("/api/workspaces/:ws/repos", () => HttpResponse.json([
        { name: "foo", state: "ready", source: { kind: "git", url: "https://gitlab.example/foo.git" } },
      ])),
    );
    renderPage("/scan/new", { type: "whitebox", workspace: "ws1", repo: "foo" });
    await waitFor(() => expect(screen.getByText("ws1")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("foo")).toBeInTheDocument());
  });

  // D3 分支删除回归：黑盒只读分支已删——黑盒 preset 到达 ScanNewPage 落到白盒渲染。
  it("黑盒预填 preset 不再触发黑盒表单（渲染白盒）", async () => {
    renderPage("/scan/new", { type: "blackbox", workspace: "ws1" });
    // PageHeader subtitle 为白盒（黑盒文案已不可达）
    expect(screen.getByText(/启动一次白盒安全审计/)).toBeInTheDocument();
    // 白盒表单（代码源步骤）在；黑盒 url 输入与跨仓表单不在
    expect(screen.getByText("代码源")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/http:\/\/example\.com/)).toBeNull();
    expect(screen.queryByTestId("corr-yaml-panel")).toBeNull();
  });

  // ── 组合扫描重跑预填（2026-09-03）：ScanList.onRerun 带 url + combined=true ──
  // combined 不预填的话，url 填了也会被 buildBody 剥掉——重跑退化纯白盒、黑盒段丢失。
  it("组合扫描 preset：combined 开关打开 + 黑盒目标 url 预填", async () => {
    server.use(
      http.get("/api/workspaces/:ws/repos", () => HttpResponse.json([
        { name: "foo", state: "ready", source: { kind: "git", url: "https://gitlab.example/foo.git" } },
      ])),
    );
    renderPage("/scan/new", {
      type: "whitebox", workspace: "ws1", repo: "foo",
      url: "https://target.example.com", combined: true,
      authProfileId: "ap1", authCredentialIds: ["cred-a"],
      hostProfileId: "hp1",
    });
    // 开关打开 + 黑盒目标 url 已填（combined=true 才展开 url 输入区）
    const toggle = screen.getByRole("switch", { name: "同时发起黑盒扫描" });
    expect(toggle).toBeChecked();
    expect(screen.getByDisplayValue("https://target.example.com")).toBeInTheDocument();
  });

  it("preset 无 combined（白盒旧 preset）→ 开关保持关闭（url 不误展开）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/repos", () => HttpResponse.json([
        { name: "foo", state: "ready", source: { kind: "git", url: "https://gitlab.example/foo.git" } },
      ])),
    );
    renderPage("/scan/new", { type: "whitebox", workspace: "ws1", repo: "foo" });
    expect(screen.getByRole("switch", { name: "同时发起黑盒扫描" })).not.toBeChecked();
  });
});

// === D3: 跨仓关联（correlation）类型切换 + 提交 body ===
describe("ScanNewPage 跨仓关联（correlation）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  // SWR 全局缓存跨测试泄漏（见 @/test/swr-render 注释）——前面用例已把 ["repos","ws1"]
  // 缓存为别的 fixture，本 describe 的提交用例需要干净缓存取自己的 repos fixture。
  function renderPageFresh(initialPath = "/scan/new") {
    return render(
      <MemoryRouter initialEntries={[initialPath]}>
        <SWRConfig value={{ provider: () => new Map() }}>
          <ScanNewPage />
        </SWRConfig>
      </MemoryRouter>,
    );
  }

  it("类型切换到跨仓关联渲染跨仓表单（含 YAML 面板），白盒表单不再渲染", async () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "跨仓关联" }));
    fireEvent.click(screen.getByTestId("corr-mode-manual"));
    // 跨仓表单：YAML 面板（默认收起）+ 工作区下拉
    expect(screen.getByTestId("corr-yaml-panel")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /YAML 配置/ })).toBeInTheDocument();
    expect(screen.queryByLabelText("YAML 编辑器")).toBeNull(); // 默认收起
    expect(screen.getByText("选择 workspace")).toBeInTheDocument();
    // 白盒表单（代码源步骤）不再渲染
    expect(screen.queryByText("代码源")).toBeNull();
    // 选 ws → 仓库卡片区解锁（添加仓库入口出现）
    await selectWorkspace("ws1");
    await waitFor(() => expect(screen.getByRole("button", { name: "+ 添加仓库" })).toBeInTheDocument());
    // 切回白盒 → 跨仓表单消失
    fireEvent.click(screen.getByRole("button", { name: "白盒扫描" }));
    expect(screen.queryByTestId("corr-yaml-panel")).toBeNull();
    expect(screen.getByText("代码源")).toBeInTheDocument();
  });

  it("提交 correlation body 含 config_content + workspace", async () => {
    let captured: Record<string, unknown> | undefined;
    server.use(
      http.get("/api/workspaces/:ws/repos", () =>
        HttpResponse.json([
          { name: "frontend", state: "ready", source: { kind: "git", url: "https://gitlab.example/frontend.git" } },
        ]),
      ),
      http.post("/api/scan", async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ workspace: "ws1", scan_id: "s-corr" }, { status: 202 });
      }),
    );
    renderPageFresh();
    fireEvent.click(screen.getByRole("button", { name: "跨仓关联" }));
    fireEvent.click(screen.getByTestId("corr-mode-manual"));
    await selectWorkspace("ws1");
    // 添加一张仓库卡（唯一卡默认 entrypoint）→ 选 frontend
    fireEvent.click(screen.getByRole("button", { name: "+ 添加仓库" }));
    fireEvent.click(screen.getByText("选择仓库"));
    fireEvent.click(await screen.findByText("frontend"));
    // 校验过（有名字的 entrypoint + ws 已选）→ 提交 enabled
    await waitFor(() => expect(screen.getByRole("button", { name: /开始关联扫描/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /开始关联扫描/ }));
    await waitFor(() => expect(captured).toBeDefined());
    expect(captured!.type).toBe("correlation");
    expect(captured!.workspace).toBe("ws1");
    expect(String(captured!.config_content)).toContain("frontend");
    expect(String(captured!.config_content)).toContain("role: entrypoint");
    // correlation 不发白盒 source / 复用字段
    expect(captured!.source).toBeUndefined();
    expect(captured!.reuse_whitebox_scan_id).toBeUndefined();
    expect(captured!.url).toBeUndefined(); // 未填 gateway url → 纯关联
  });
});

// === MR 增量扫描（spec 2026-09-03）：type=mr 表单渲染 + 校验 + 提交 body ===
describe("ScanNewPage MR 增量扫描", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  // SWR 缓存跨用例隔离（同 correlation describe 的 renderPageFresh 注释）
  function renderPageFresh(state?: unknown) {
    return render(
      <MemoryRouter initialEntries={[state ? { pathname: "/scan/new", state } : "/scan/new"]}>
        <SWRConfig value={{ provider: () => new Map() }}>
          <ScanNewPage />
        </SWRConfig>
      </MemoryRouter>,
    );
  }

  /** 切到 MR 并选 ws（repos fixture 已由调用方 server.use 注入时再选仓库） */
  async function switchToMrAndSelectWs() {
    fireEvent.click(screen.getByRole("button", { name: "MR 增量扫描" }));
    await selectWorkspace("ws1");
  }

  it("类型切换到 MR 渲染 MR 表单（ws + 仓库 + base/head），不落跨仓拓扑表单", async () => {
    renderPage();
    // corrMode 初始恒 "auto"——MR 分支必须排在它之前，否则错渲染跨仓拓扑表单（进度 §3.1 的缺口）
    fireEvent.click(screen.getByRole("button", { name: "MR 增量扫描" }));
    const form = screen.getByTestId("mr-form");
    expect(screen.queryByTestId("corr-yaml-panel")).toBeNull();
    expect(screen.queryByText("自动拓扑")).toBeNull();
    // 白盒表单（步骤分组）不再渲染
    expect(screen.queryByText("目标服务")).toBeNull();
    // 未选 ws → 先选 workspace 提示（repo/refs 区未解锁）
    expect(screen.getByText(/请先选择 workspace/)).toBeInTheDocument();
    // 选 ws → 仓库下拉 + 链接导入 + base/head 区间控件出现（提示消失）
    await switchToMrAndSelectWs();
    await waitFor(() => expect(screen.queryByText(/请先选择 workspace/)).toBeNull());
    expect(within(form).getByText("选择仓库")).toBeInTheDocument();
    expect(within(form).getByText("从 MR 链接导入")).toBeInTheDocument();
    expect(within(form).getByText("变更范围")).toBeInTheDocument();
    expect(within(form).getByLabelText("Base")).toBeInTheDocument();
    expect(within(form).getByLabelText("Head")).toBeInTheDocument();
  });

  it("MR 校验：repo/base/head 缺一即拦（错误文案 + 提交 disabled）；补齐后可提交", async () => {
    server.use(
      http.get("/api/workspaces/:ws/repos", () =>
        HttpResponse.json([
          { name: "nodegoat", state: "ready", source: { kind: "git", url: "https://gitlab.example/nodegoat.git" } },
        ]),
      ),
    );
    renderPageFresh();
    await switchToMrAndSelectWs();
    await waitFor(() => screen.getByText("选择仓库"));
    const submit = screen.getByRole("button", { name: /开始扫描/ });
    // 未选 repo、未填 refs → 三错误齐显 + disabled
    expect(screen.getByText("请选择仓库")).toBeInTheDocument();
    expect(screen.getByText("请填写 base 与 head 引用")).toBeInTheDocument();
    expect(submit).toBeDisabled();
    // 选 repo、只填 base → refs 错误仍在
    fireEvent.click(screen.getByText("选择仓库"));
    fireEvent.click(await screen.findByText("nodegoat"));
    await waitFor(() => expect(screen.queryByText("请选择仓库")).toBeNull());
    fireEvent.change(screen.getByTestId("mr-base-ref"), { target: { value: "main" } });
    expect(screen.getByText("请填写 base 与 head 引用")).toBeInTheDocument();
    expect(submit).toBeDisabled();
    // 补 head → 全部错误消失 + 就绪摘要（base..head）出现 + enabled
    fireEvent.change(screen.getByTestId("mr-head-ref"), { target: { value: "feature/xss" } });
    expect(screen.queryByText("请填写 base 与 head 引用")).toBeNull();
    expect(screen.getByTestId("mr-range-summary")).toHaveTextContent("main..feature/xss");
    expect(submit).toBeEnabled();
  });

  it("提交 mr body 含 type/source/base_ref/head_ref，无 url/认证字段", async () => {
    let captured: Record<string, unknown> | undefined;
    server.use(
      http.get("/api/workspaces/:ws/repos", () =>
        HttpResponse.json([
          { name: "nodegoat", state: "ready", source: { kind: "git", url: "https://gitlab.example/nodegoat.git" } },
        ]),
      ),
      http.post("/api/scan", async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ workspace: "ws1", scan_id: "nodegoat-mr-1" }, { status: 202 });
      }),
    );
    renderPageFresh();
    await switchToMrAndSelectWs();
    await waitFor(() => screen.getByText("选择仓库"));
    fireEvent.click(screen.getByText("选择仓库"));
    fireEvent.click(await screen.findByText("nodegoat"));
    fireEvent.change(screen.getByTestId("mr-base-ref"), { target: { value: "abc1234" } });
    fireEvent.change(screen.getByTestId("mr-head-ref"), { target: { value: "def5678" } });
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(captured).toBeDefined());
    expect(captured!.type).toBe("mr");
    expect(captured!.workspace).toBe("ws1");
    expect(captured!.source).toEqual({ kind: "repo", value: "nodegoat" });
    expect(captured!.base_ref).toBe("abc1234");
    expect(captured!.head_ref).toBe("def5678");
    // 纯白盒语义：无 url / 认证 / HOST 字段
    expect(captured!.url).toBeUndefined();
    expect(captured!.authentication).toBeUndefined();
    expect(captured!.auth_profile_id).toBeUndefined();
  });

  it("MR 重跑预填（location.state）：直达 MR 表单 + repo/refs 回填", async () => {
    server.use(
      http.get("/api/workspaces/:ws/repos", () =>
        HttpResponse.json([
          { name: "nodegoat", state: "ready", source: { kind: "git", url: "https://gitlab.example/nodegoat.git" } },
        ]),
      ),
    );
    renderPageFresh({
      type: "mr", workspace: "ws1", repo: "nodegoat",
      mrBaseRef: "main", mrHeadRef: "feature/xss",
    });
    // preset.type="mr" 直达 MR 表单（不落白盒默认）
    expect(screen.getByTestId("mr-form")).toBeInTheDocument();
    // repo 预填（RepoCombobox 显选中值——等 repos 拉回后 placeholder 换成选中名）；refs 原样回填
    await waitFor(() => expect(screen.getByText("nodegoat")).toBeInTheDocument());
    expect((screen.getByTestId("mr-base-ref") as HTMLInputElement).value).toBe("main");
    expect((screen.getByTestId("mr-head-ref") as HTMLInputElement).value).toBe("feature/xss");
  });
});

describe("ScanNewPage 配色 · coral 收窄到点缀（对齐全站克制基调）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("右侧信息侧栏已移除（无「审计范围」/「攻击面」）", () => {
    renderPage();
    expect(screen.queryByText("审计范围")).toBeNull();
    expect(screen.queryByText("攻击面")).toBeNull();
  });

  it("底部操作栏用 bg-card（去 secondary 灰堆叠）", () => {
    renderPage();
    const footer = screen.getByRole("button", { name: /开始扫描/ }).parentElement;
    expect(footer?.className).not.toMatch(/bg-secondary/);
    expect(footer?.className).toMatch(/bg-card/);
  });
});

// === 黑盒登录配置：buildAuthPayload（AuthFormState → ScanAuthentication 契约转换）+ validateAuth ===
describe("黑盒登录 buildAuthPayload / validateAuth", () => {
  const base: AuthFormState = {
    enabled: true, source: "inline", profileId: "", credentialIds: [],
    loginType: "form", loginUrl: "https://x/login",
    accounts: [{ role: "admin", username: "admin", password: "pw" }], loginFlow: "a\nb",
  };
  // t 只回 key（断言用 key 本身，不依赖 i18n 文案）
  const t = ((k: string) => k) as never;

  it("buildAuthPayload → ScanAuthentication（snake_case + login_flow 按行 split）", () => {
    const p = buildAuthPayload({ ...base });
    expect(p.login_type).toBe("form");
    expect(p.login_url).toBe("https://x/login");
    expect(p.credentials).toEqual({ username: "admin", password: "pw" });
    expect(p.login_flow).toEqual(["a", "b"]);
  });

  it("loginFlow 全空行 → 不发 login_flow 字段", () => {
    const p = buildAuthPayload({ ...base, loginFlow: "  \n \n" });
    expect(p.login_flow).toBeUndefined();
  });

  it("buildAuthPayload 不含 email_login（微调：inline 不再采集邮箱登录）", () => {
    const p = buildAuthPayload({ ...base });
    expect(p.credentials.email_login).toBeUndefined();
  });

  it("validateAuth: disabled → null（不校验）", () => {
    expect(validateAuth({ ...base, enabled: false }, t)).toBeNull();
  });

  it("validateAuth: enabled 缺 loginUrl → authLoginUrlEmpty", () => {
    expect(validateAuth({ ...base, loginUrl: "" }, t)).toBe("scan.errors.authLoginUrlEmpty");
  });

  it("validateAuth: enabled loginUrl 非 http(s) → authLoginUrl", () => {
    expect(validateAuth({ ...base, loginUrl: "ftp://x" }, t)).toBe("scan.errors.authLoginUrl");
  });

  it("validateAuth: primary（accounts[0]）缺用户名 → authUsername", () => {
    expect(validateAuth({ ...base, accounts: [
      { role: "admin", username: "", password: "pw" },
    ] }, t)).toBe("scan.errors.authUsername");
  });

  it("validateAuth: 附加角色缺用户名/密码 → authAccountIncomplete", () => {
    expect(validateAuth({ ...base, accounts: [
      { role: "admin", username: "admin", password: "pw" },
      { role: "user", username: "", password: "" },
    ] }, t)).toBe("scan.errors.authAccountIncomplete");
  });

  // === auth-profile-vault Task 14：profile 模式校验（多角色子集 2026-08-06） ===
  it("validateAuth: profile 模式缺 profileId → authProfileRequired", () => {
    expect(validateAuth({ ...base, source: "profile", profileId: "", credentialIds: [] }, t))
      .toBe("scan.errors.authProfileRequired");
  });

  it("validateAuth: profile 模式有 profileId 缺 credentialIds → authCredentialRequired", () => {
    expect(validateAuth({ ...base, source: "profile", profileId: "p1", credentialIds: [] }, t))
      .toBe("scan.errors.authCredentialRequired");
  });

  it("validateAuth: profile 模式 profileId+credentialIds 齐 → null", () => {
    expect(validateAuth({ ...base, source: "profile", profileId: "p1", credentialIds: ["c1"] }, t))
      .toBeNull();
  });

  it("presetToAuthState: authProfileId 非空 → source=profile + ids（auth 不读）", () => {
    const state = presetToAuthState({
      authProfileId: "p1", authCredentialIds: ["c1"],
      // 故意同时给 auth:inline——profile 优先，应忽略
      auth: { login_type: "form", login_url: "http://x", credentials: { username: "u" } },
    });
    expect(state.enabled).toBe(true);
    expect(state.source).toBe("profile");
    expect(state.profileId).toBe("p1");
    expect(state.credentialIds).toEqual(["c1"]);
  });

  it("presetToAuthState: 仅 auth（inline）→ source=inline + authFromPayload", () => {
    const state = presetToAuthState({
      auth: { login_type: "form", login_url: "http://x/l", credentials: { username: "u", password: "p" } },
    });
    expect(state.enabled).toBe(true);
    expect(state.source).toBe("inline");
    expect(state.loginUrl).toBe("http://x/l");
    expect(state.accounts[0]?.username).toBe("u");
  });

  it("presetToAuthState: 空 preset → DEFAULT_AUTH（disabled, profile 默认）", () => {
    const state = presetToAuthState({});
    expect(state.enabled).toBe(false);
    expect(state.source).toBe("profile");
  });
});

// === Task 13: HOST 解析（host_profile_id / host_url）buildBody + presetToHostState ===
// 镜像 auth 的 buildBody/presetToAuthState 单测范式——纯函数断言字段映射，不渲染。
// HOST 与 auth 独立（非互斥）：enabled 才发对应字段，disabled 不发（向后兼容，不起代理）。
// D3 起黑盒分支已删——经 correlation 分支（gateway url 非空 = 段③黑盒验证）覆盖同一套 assignHostToBody。
describe("correlation HOST buildBody / presetToHostState", () => {
  // 最小可用 correlation FormState（gateway url 开 = 附黑盒验证；auth/host 默认 disabled）。
  const baseF: FormState = {
    selectedRepo: "",
    url: "http://example.com",
    reuseScanId: "",
    auth: { enabled: false, source: "inline", profileId: "", credentialIds: [],
      loginType: "form", loginUrl: "", accounts: [{ role: "admin", username: "", password: "" }], loginFlow: "" },
    host: { enabled: false, mode: "profile", profileId: "", hostUrl: "" },
    yaml: "",
  };
  const CORR_YAML = "repos:\n  frontend:\n    path: frontend\n    role: entrypoint\n";

  it("buildBody correlation: config_content 透传 + 无 gateway url → 不发 url/认证/HOST（纯关联）", () => {
    const body = buildBody("correlation", { ...baseF, url: "" }, "ws1", CORR_YAML);
    expect(body.type).toBe("correlation");
    expect(body.workspace).toBe("ws1");
    expect(body.config_content).toBe(CORR_YAML);
    expect(body.url).toBeUndefined();
    expect(body.authentication).toBeUndefined();
    expect(body.host_url).toBeUndefined();
    expect(body.source).toBeUndefined();
  });

  it("buildBody: host disabled → 不发 host_profile_id / host_url（向后兼容）", () => {
    const body = buildBody("correlation", baseF, "ws1", CORR_YAML);
    expect(body.host_profile_id).toBeUndefined();
    expect(body.host_url).toBeUndefined();
  });

  it("buildBody: host enabled + profile 模式 → 发 host_profile_id（无 host_url）", () => {
    const body = buildBody("correlation",
      { ...baseF, host: { enabled: true, mode: "profile", profileId: "host_1", hostUrl: "" } }, "ws1", CORR_YAML);
    expect(body.host_profile_id).toBe("host_1");
    expect(body.host_url).toBeUndefined();
  });

  it("buildBody: host enabled + url 模式 → 发 host_url（无 host_profile_id）", () => {
    const body = buildBody("correlation",
      { ...baseF, host: { enabled: true, mode: "url", profileId: "", hostUrl: "https://x/hosts.txt" } }, "ws1", CORR_YAML);
    expect(body.host_url).toBe("https://x/hosts.txt");
    expect(body.host_profile_id).toBeUndefined();
  });

  it("buildBody: host enabled + profile 模式 profileId 空 → 拒绝静默降级", () => {
    expect(() => buildBody("correlation",
      { ...baseF, host: { enabled: true, mode: "profile", profileId: "", hostUrl: "" } }, "ws1", CORR_YAML))
      .toThrow("scan.errors.hostProfileRequired");
  });

  it("buildBody: host 与 auth 独立——两者同时 enabled 各发各的字段（非互斥）", () => {
    const body = buildBody("correlation", {
      ...baseF,
      auth: { enabled: true, source: "inline", profileId: "", credentialIds: [],
        loginType: "form", loginUrl: "http://t/login",
        accounts: [{ role: "admin", username: "u", password: "p" }], loginFlow: "" },
      host: { enabled: true, mode: "url", profileId: "", hostUrl: "https://x/hosts.txt" },
    }, "ws1", CORR_YAML);
    expect(body.url).toBe("http://example.com"); // gateway url 非空 → 附黑盒验证
    expect(body.authentication).toBeDefined(); // auth inline 仍发
    expect(body.host_url).toBe("https://x/hosts.txt"); // host 同时发
  });

  it("presetToHostState: hostProfileId 非空 → enabled + profile 模式", () => {
    const state = presetToHostState({ hostProfileId: "host_1" } as RerunPreset);
    expect(state.enabled).toBe(true);
    expect(state.mode).toBe("profile");
    expect(state.profileId).toBe("host_1");
    expect(state.hostUrl).toBe("");
  });

  it("presetToHostState: 仅 hostUrl → enabled + url 模式", () => {
    const state = presetToHostState({ hostUrl: "https://x/hosts.txt" } as RerunPreset);
    expect(state.enabled).toBe(true);
    expect(state.mode).toBe("url");
    expect(state.hostUrl).toBe("https://x/hosts.txt");
  });

  it("presetToHostState: 空 preset → DEFAULT_HOST（disabled）", () => {
    const state = presetToHostState({} as RerunPreset);
    expect(state.enabled).toBe(false);
    expect(state.mode).toBe("profile");
  });

  it("presetToHostState: hostProfileId 优先于 hostUrl（profile 优先）", () => {
    const state = presetToHostState({ hostProfileId: "host_1", hostUrl: "https://x/hosts.txt" } as RerunPreset);
    expect(state.mode).toBe("profile");
    expect(state.profileId).toBe("host_1");
  });
});

// === 组合扫描 HOST 字段透传（2026-08-13：白盒组合开关展开 HOST 入口后 buildBody 须发 host） ===
// 与黑盒分支同款 assignHostToBody：enabled 时按 mode 发 host_profile_id / host_url；
// disabled 不发；非组合白盒（combined=false）即便 host.enabled 也不发（纯白盒无黑盒阶段）。
describe("buildBody whitebox 组合扫描 HOST 透传", () => {
  function wbForm(overrides: Partial<FormState> = {}): FormState {
    return {
      selectedRepo: "foo",
      url: "http://target.example/",
      reuseScanId: "",
      auth: {
        enabled: false, source: "inline", profileId: "", credentialIds: [],
        loginType: "form", loginUrl: "",
        accounts: [{ role: "admin", username: "", password: "" }], loginFlow: "",
      },
      host: { enabled: true, mode: "profile", profileId: "", hostUrl: "" },
      yaml: "",
      combined: true,
      ...overrides,
    };
  }

  it("组合 + host profile 模式 -> 发 host_profile_id（不发 host_url）", () => {
    const body = buildBody("whitebox", wbForm({
      host: { enabled: true, mode: "profile", profileId: "host_1", hostUrl: "" },
    }), "ws1");
    expect(body.host_profile_id).toBe("host_1");
    expect(body.host_url).toBeUndefined();
  });

  it("组合 + host url 模式 -> 发 host_url（不发 host_profile_id）", () => {
    const body = buildBody("whitebox", wbForm({
      host: { enabled: true, mode: "url", profileId: "", hostUrl: "https://x/hosts.txt" },
    }), "ws1");
    expect(body.host_url).toBe("https://x/hosts.txt");
    expect(body.host_profile_id).toBeUndefined();
  });

  it("组合 + host disabled -> 不发任何 host 字段（直连目标，向后兼容）", () => {
    const body = buildBody("whitebox", wbForm({
      host: { enabled: false, mode: "profile", profileId: "host_1", hostUrl: "" },
    }), "ws1");
    expect(body.host_profile_id).toBeUndefined();
    expect(body.host_url).toBeUndefined();
  });

  it("组合 + host profile 但 profileId 空 -> 拒绝静默降级", () => {
    expect(() => buildBody("whitebox", wbForm({
      host: { enabled: true, mode: "profile", profileId: "", hostUrl: "" },
    }), "ws1")).toThrow("scan.errors.hostProfileRequired");
  });

  it("非组合白盒（combined=false）即便 host enabled 也不发 host（纯白盒无黑盒阶段）", () => {
    const body = buildBody("whitebox", wbForm({
      combined: false,
      host: { enabled: true, mode: "profile", profileId: "host_1", hostUrl: "" },
    }), "ws1");
    expect(body.host_profile_id).toBeUndefined();
    expect(body.host_url).toBeUndefined();
  });
});


describe("HOST enabled source validation", () => {
  const baseF: FormState = {
    selectedRepo: "",
    url: "http://example.com",
    reuseScanId: "20260731-1200",
    auth: { enabled: false, source: "inline", profileId: "", credentialIds: [],
      loginType: "form", loginUrl: "", accounts: [{ role: "admin", username: "", password: "" }], loginFlow: "" },
    host: { enabled: true, mode: "profile", profileId: "", hostUrl: "" },
    yaml: "",
  };

  it("does not silently drop an enabled HOST source", () => {
    expect(() => buildBody("correlation", baseF, "ws1")).toThrow();
  });

  it("rejects an enabled HOST URL with a non-http(s) scheme", () => {
    expect(() => buildBody("correlation", {
      ...baseF,
      host: { enabled: true, mode: "url", profileId: "", hostUrl: "ftp://hosts.example/hosts" },
    }, "ws1")).toThrow();
  });
});

describe("correlation topology auto flow", () => {
  function renderAutoPage() {
    return render(
      <MemoryRouter initialEntries={["/scan/new"]}>
        <SWRConfig value={{ provider: () => new Map() }}>
          <ScanNewPage />
        </SWRConfig>
      </MemoryRouter>,
    );
  }
  it("analyzes, confirms topology, gates submission, and posts confirmed YAML", async () => {
    let submitted: Record<string, unknown> | undefined;
    server.use(
      // 隔离刷新恢复：本用例从"无历史分析"出发（默认 latest 404 会被下面的 :id
      // 动态段拦截——msw use 优先且 :id 匹配字面量 latest——须显式再 use 一个 404）。
      http.get("/api/workspaces/:ws/correlation-topology/analyses/latest", () =>
        new HttpResponse(null, { status: 404 })),
      http.get("/api/workspaces/:ws/repos", () => HttpResponse.json([
        { name: "web", state: "ready" }, { name: "order", state: "ready" },
        { name: "admin", state: "ready" }, { name: "user", state: "ready" },
      ])),
      http.post("/api/workspaces/:ws/correlation-topology/analyses", async ({ request }) => {
        const body = await request.json() as { repos: string[]; refresh?: boolean };
        expect(body.repos).toEqual(["web", "order", "admin", "user"]);
        return HttpResponse.json({ analysis_id: "topology-1" }, { status: 202 });
      }),
      http.get("/api/workspaces/:ws/correlation-topology/analyses/:id", () => HttpResponse.json({
        analysis_id: "topology-1", workspace: "ws1", status: "completed",
        repos: ["web", "order"], cache_hit: false,
        result: {
          nodes: [{ repo: "web", roles: ["entrypoint", "backend"], capabilities: [] },
            { repo: "order", roles: ["backend"], capabilities: [] },
            { repo: "admin", roles: ["entrypoint"], capabilities: [] },
            { repo: "user", roles: ["backend"], capabilities: [] }],
          edges: [
            { from: "web", to: "order", protocol: "grpc", confidence: "high",
              client_evidence: [], handler_evidence: [] },
            { from: "admin", to: "order", protocol: "graphql", confidence: "medium",
              client_evidence: [], handler_evidence: [] },
            { from: "admin", to: "user", protocol: "http", confidence: "medium",
              client_evidence: [], handler_evidence: [] },
          ],
          uncertain: [], coverage: [], invalid: [],
        },
      })),
      http.post("/api/scan", async ({ request }) => {
        submitted = await request.json() as Record<string, unknown>;
        return HttpResponse.json({ workspace: "ws1", scan_id: "scan-1" });
      }),
    );
    renderAutoPage();
    fireEvent.click(screen.getByTestId("scan-type-correlation"));
    await selectWorkspace("ws1");
    fireEvent.click(await screen.findByRole("checkbox", { name: /web/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /order/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /admin/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /user/ }));
    fireEvent.click(screen.getByRole("button", { name: /自动关联分析/ }));
    expect(await screen.findByTestId("topology-node-web")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /添加关系/ }));
    // 边表下拉已换 ui/Select（Radix）：click trigger → click option（原生 select change 不再生效）
    const fromSelects = screen.getAllByRole("combobox", { name: / from$/ });
    fireEvent.click(fromSelects.at(-1)!);
    fireEvent.click(await screen.findByRole("option", { name: "web" }));
    const toSelects = screen.getAllByRole("combobox", { name: / to$/ });
    fireEvent.click(toSelects.at(-1)!);
    fireEvent.click(await screen.findByRole("option", { name: "user" }));
    const protocols = screen.getAllByRole("combobox", { name: "protocol" });
    fireEvent.click(protocols.at(-1)!);
    fireEvent.click(await screen.findByRole("option", { name: "http" }));
    expect(screen.getByRole("button", { name: /启动跨仓扫描/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /确认拓扑/ }));
    expect(screen.getByRole("button", { name: /启动跨仓扫描/ })).toBeEnabled();
    // Manual fallback preserves the edited graph before confirmation.
    fireEvent.click(screen.getAllByRole("button", { name: /手工模式/ }).at(-1)!);
    expect(screen.getAllByTestId("corr-relation-chip").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByTestId("corr-mode-auto"));
    fireEvent.click(screen.getByRole("button", { name: /确认拓扑/ }));
    expect(screen.getByRole("button", { name: /启动跨仓扫描/ })).toBeEnabled();

    // An unapplied YAML edit must invalidate confirmation and cannot bypass submission.
    fireEvent.click(screen.getByRole("button", { name: /YAML 配置/ }));
    const editor = screen.getByLabelText("YAML 编辑器");
    const confirmedYaml = (editor as HTMLTextAreaElement).value;
    fireEvent.change(editor, { target: { value: `${confirmedYaml}\n# unconfirmed edit` } });
    expect(screen.getByRole("button", { name: /启动跨仓扫描/ })).toBeDisabled();
    fireEvent.change(editor, { target: { value: confirmedYaml } });
    expect(screen.getByRole("button", { name: /启动跨仓扫描/ })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /启动跨仓扫描/ }));
    await waitFor(() => expect(submitted).toBeDefined());
    const submittedYaml = String(submitted!.config_content);
    expect(submittedYaml).toContain("from: web");
    expect(submittedYaml).toContain("to: order");
    expect(submittedYaml).toContain("from: admin");
    expect(submittedYaml).toContain("to: user");
    expect(submittedYaml).toContain("roles:\n      - entrypoint\n      - backend");
  });

  it("刷新恢复：选 ws 后找回 running 分析，恢复状态轮询并显示过程日志", async () => {
    server.use(
      http.get("/api/workspaces/:ws/correlation-topology/analyses/latest", () =>
        HttpResponse.json({
          analysis_id: "topology-r1", workspace: "ws1", status: "running",
          repos: ["web", "order"], progress: 20,
        })),
      http.get("/api/workspaces/:ws/correlation-topology/analyses/:id", () =>
        HttpResponse.json({
          analysis_id: "topology-r1", workspace: "ws1", status: "running",
          repos: ["web", "order"], progress: 20,
        })),
      http.get("/api/workspaces/:ws/correlation-topology/analyses/:id/log", () =>
        HttpResponse.json({
          lines: [
            { no: 0, ts: "2026-09-03T18:00:00Z", type: "tool_start", tool: "grep", summary: "pattern=identity" },
            { no: 1, ts: "2026-09-03T18:00:02Z", type: "assistant_turn", summary: "turn 2: tracing gateway→identity" },
          ],
          next: 1,
        })),
    );
    renderAutoPage();
    fireEvent.click(screen.getByTestId("scan-type-correlation"));
    await selectWorkspace("ws1");
    // 面板恢复为 running（取消按钮语义）且日志尾窗出现
    expect(await screen.findByRole("button", { name: /取消/ })).toBeInTheDocument();
    expect(await screen.findByText(/tracing gateway→identity/)).toBeInTheDocument();
    expect(screen.getByText(/pattern=identity/)).toBeInTheDocument();
  });
});

describe("ScanNewPage 链接解析（resolve-link 回填，2026-09-03 仓库入口整合 B 段）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  function renderPageFresh(state?: unknown) {
    return render(
      <MemoryRouter initialEntries={[state ? { pathname: "/scan/new", state } : "/scan/new"]}>
        <SWRConfig value={{ provider: () => new Map() }}>
          <ScanNewPage />
        </SWRConfig>
      </MemoryRouter>,
    );
  }

  const REPOS_READY = () =>
    http.get("/api/workspaces/:ws/repos", () =>
      HttpResponse.json([
        { name: "nodegoat", state: "ready", source: { kind: "git", url: "https://gitlab.example/nodegoat.git" } },
      ]),
    );

  function mockResolve(body: Record<string, unknown>, status = 200) {
    return http.post("/api/workspaces/:ws/resolve-link", () =>
      HttpResponse.json(body, { status }));
  }

  async function resolveLink(url: string) {
    fireEvent.change(screen.getByTestId("link-url-input"), { target: { value: url } });
    fireEvent.click(screen.getByTestId("link-resolve-btn"));
  }

  it("白盒表单粘 MR 链接：自动切到 MR + 回填 refs + 选中仓库", async () => {
    server.use(
      REPOS_READY(),
      mockResolve({ kind: "mr", repo: "nodegoat", base_ref: "main", head_ref: "feature/xss", repo_state: "ready" }),
    );
    renderPageFresh();
    await selectWorkspace("ws1");
    // 白盒表单（Step2 代码源）内有链接框
    await waitFor(() => expect(screen.getByTestId("link-url-input")).toBeInTheDocument());
    await resolveLink("https://gitlab.example.com/nodegoat/-/merge_requests/42");
    // 自动切类型：MR 表单渲染 + refs 回填 + repo 选中
    await waitFor(() => expect(screen.getByTestId("mr-form")).toBeInTheDocument());
    expect((screen.getByTestId("mr-base-ref") as HTMLInputElement).value).toBe("main");
    expect((screen.getByTestId("mr-head-ref") as HTMLInputElement).value).toBe("feature/xss");
    await waitFor(() => expect(screen.getByText("nodegoat")).toBeInTheDocument());
  });

  it("MR 表单粘仓库链接：提示切白盒，不切类型不回填", async () => {
    server.use(
      REPOS_READY(),
      mockResolve({ kind: "repo", repo: "nodegoat", repo_state: "ready" }),
    );
    renderPageFresh();
    fireEvent.click(screen.getByRole("button", { name: "MR 增量扫描" }));
    await selectWorkspace("ws1");
    await waitFor(() => expect(screen.getByTestId("mr-form")).toBeInTheDocument());
    await resolveLink("https://gitlab.example.com/nodegoat.git");
    await waitFor(() => expect(screen.getByText("检测到仓库链接，请切换到白盒扫描类型使用")).toBeInTheDocument());
    // 未切类型（MR 表单仍在）、refs 未回填
    expect(screen.getByTestId("mr-form")).toBeInTheDocument();
    expect((screen.getByTestId("mr-base-ref") as HTMLInputElement).value).toBe("");
  });

  it("解析失败：行内显示后端错误文案，不阻塞手填", async () => {
    server.use(
      REPOS_READY(),
      mockResolve({ detail: "检测到 GitHub PR 链接，暂仅支持 GitLab MR 链接" }, 422),
    );
    renderPageFresh();
    fireEvent.click(screen.getByRole("button", { name: "MR 增量扫描" }));
    await selectWorkspace("ws1");
    await waitFor(() => expect(screen.getByTestId("mr-form")).toBeInTheDocument());
    await resolveLink("https://github.com/foo/bar/pull/1");
    await waitFor(() => expect(screen.getByText("检测到 GitHub PR 链接，暂仅支持 GitLab MR 链接")).toBeInTheDocument());
    // 手填不受影响
    fireEvent.change(screen.getByTestId("mr-base-ref"), { target: { value: "main" } });
    expect((screen.getByTestId("mr-base-ref") as HTMLInputElement).value).toBe("main");
  });

  it("cloning：立即选中仓库并显示下载提示，轮询 ready 后提示消失", async () => {
    // repos 按 repoReady 标志响应（非计数器——mrRepos key 随类型切换激活会重发请求，
    // 计数器时序不稳：CloneWatch 挂载时可能已拿到 ready，提示一闪而过抓不到）。
    let repoReady = false;
    server.use(
      http.get("/api/workspaces/:ws/repos", () =>
        HttpResponse.json([
          { name: "nodegoat", state: repoReady ? "ready" : "cloning", source: { kind: "git", url: "https://gitlab.example/nodegoat.git" } },
        ]),
      ),
      mockResolve({ kind: "mr", repo: "nodegoat", base_ref: "main", head_ref: "feature/xss", repo_state: "cloning" }),
    );
    renderPageFresh();
    await selectWorkspace("ws1");
    await waitFor(() => expect(screen.getByTestId("link-url-input")).toBeInTheDocument());
    await resolveLink("https://gitlab.example.com/nodegoat/-/merge_requests/42");
    // 切到 MR + refs 回填 + 仓库已选中（cloning 也立即选中）
    await waitFor(() => expect(screen.getByTestId("mr-form")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("nodegoat")).toBeInTheDocument());
    // 下载中提示出现（CloneWatch 轮询中）
    await waitFor(() => expect(screen.getByText(/正在下载仓库/)).toBeInTheDocument());
    // 标志翻转 → 下一次轮询（2s 间隔）拉到 ready → 提示消失
    repoReady = true;
    await waitFor(() => expect(screen.queryByText(/正在下载仓库/)).toBeNull(), { timeout: 6000 });
  });
});
