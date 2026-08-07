import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import i18n from "@/i18n";
import { ScanNewPage, buildAuthPayload, validateAuth, presetToAuthState, type AuthFormState } from "./ScanNewPage";

// Monaco 在测试里替换成 textarea（data-testid="monaco"），同 YamlEditor.test 模式
vi.mock("@monaco-editor/react", () => ({
  default: ({ value, onChange }: { value: string; onChange?: (v: string) => void }) => (
    <textarea data-testid="monaco" value={value} onChange={(e) => onChange?.(e.target.value)} />
  ),
}));

// 空态提示按 role 切文案 → useAuth 可控（同 DashboardPage.test 模式）。
const { mockUseAuth } = vi.hoisted(() => ({ mockUseAuth: vi.fn() }));
vi.mock("@/auth/AuthContext", () => ({ useAuth: () => mockUseAuth() }));

// P2: 扫描目标 ws 必须从下拉选——选项来自 /workspaces（P1 后端已按当前用户可见性过滤）。
// 默认 ws 列表覆盖 ws1 / ws2 两个，模拟用户已有 ws 的常见态。
const WS_LIST = [
  { name: "ws1", scan_type: "whitebox", status: "completed", created_at: 0 },
  { name: "ws2", scan_type: "blackbox", status: "completed", created_at: 0 },
];

// 黑盒「复用白盒结果」候选 fixture：ws1 内一条已完成的白盒扫描。
const WB_SCANS = [
  { scan_id: "20260731-1200", scan_type: "whitebox", status: "completed", created_at: 1722400000, vuln_count: 3, is_running: false, workflow_id: "ws1-foo-20260731-1200" },
];

const userUser = { id: 1, username: "alice", role: "user", must_change_password: false };
const userAdmin = { id: 2, username: "root", role: "admin", must_change_password: false };

const server = setupServer(
  http.get("/api/workspaces", () => HttpResponse.json(WS_LIST)),
  // P2: repo 已迁到 ws 内——默认空列表，repo 相关用例各自 server.use 注入。
  http.get("/api/workspaces/:ws/repos", () => HttpResponse.json([])),
  // 黑盒复用候选：默认该 ws 无 whitebox 扫描（驱动智能默认退到 repo 模式）。
  http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([])),
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

// #1 单一 disclosure（展开即启用）：「需要登录」Switch 已移除，改用状态文案探测 enabled
// （statusEnabled=「当前：已启用登录」/ statusUnauth=「当前：以未登录状态扫描」随 enabled 翻转）。
async function expectAuthEnabled() {
  await waitFor(() => expect(screen.getByText(/已启用登录/)).toBeInTheDocument());
}

// RepoCombobox 在某 StepGroup 内：白盒 Step2="代码源"，黑盒 repo 模式 Step3="代码上下文"。
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

  it("切联动：显示 yaml 编辑器，隐藏白盒字段", () => {
    renderPage();
    clickTab("联动");
    expect(screen.getByTestId("monaco")).toBeInTheDocument();
    // 联动页不显示「选择仓库」fieldset
    expect(screen.queryByText(/\+ 添加新仓库/)).toBeNull();
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
          { detail: [{ loc: ["body", "config_yaml"], msg: "repo url required", type: "value_error" }] },
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

// === 黑盒「复用白盒结果」（恒复用——exploitation-only，无 repo/standalone 分支）===
describe("ScanNewPage 黑盒代码上下文（恒复用白盒）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("ws 有白盒扫描 → 自动选最新；不填 url → disabled，填齐 → enabled；buildBody 发 reuse_whitebox_scan_id（无 source）", async () => {
    let captured: Record<string, unknown> | undefined;
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json(WB_SCANS)),
      http.post("/api/scan", async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ workspace: "ws1" }, { status: 202 });
      }),
    );
    renderPage();
    clickTab("黑盒");
    await selectWorkspace("ws1");
    // 有白盒扫描 → 自动选最新一条 → trigger 显其 workflow_id
    await waitFor(() => expect(screen.getByText(/ws1-foo-20260731-1200/)).toBeInTheDocument());
    // reuseScanId 已自动选，但 url 必填 → 不填时提交 disabled
    expect(screen.getByRole("button", { name: /开始渗透/ })).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
    await waitFor(() => expect(screen.getByRole("button", { name: /开始渗透/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /开始渗透/ }));
    await waitFor(() => expect(captured).toBeDefined());
    expect(captured!.reuse_whitebox_scan_id).toBe("20260731-1200");
    expect(captured!.source).toBeUndefined();
  });

  it("ws 无白盒扫描 → 显复用空态引导 + 不显仓库选择器；填 url 仍 disabled（reuseScanId 必填）", async () => {
    renderPage();
    clickTab("黑盒");
    await selectWorkspace("ws1");
    // 默认 /scans 空 → 黑盒恒复用白盒，无白盒则显空态（不退到 repo）
    expect(await screen.findByText(/还没有白盒扫描结果/)).toBeInTheDocument();
    // 无 repo 选择器（不再有「指定仓库」分支）
    expect(screen.queryByRole("button", { name: /\+ 添加新仓库/ })).toBeNull();
    // 即便填 url，reuseScanId 必填不可满足 → 提交 disabled
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
    expect(screen.getByRole("button", { name: /开始渗透/ })).toBeDisabled();
  });
});

// === auth-profile-vault Task 14：profile 模式（选档案+多角色，buildBody 发 auth_profile_id+auth_credential_ids） ===
describe("ScanNewPage 黑盒认证档案库（profile 模式）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  const PROFILE_FIXTURE = [
    {
      id: "prof_1", name: "NG", login_url: "http://t/", login_type: "form",
      credentials: [{
        id: "cred_a", role: "admin", username: "a",
        verify_status: { state: "unverified" as const },
      }],
    },
  ];

  // 黑盒 profile 模式：选档案 → 角色默认全选 → buildBody 发 auth_profile_id+auth_credential_ids，无 authentication。
  it("profile 模式选档案默认全选 → 发 auth_profile_id + auth_credential_ids（无 authentication）", async () => {
    let posted: Record<string, unknown> | undefined;
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json(WB_SCANS)),
      http.get("/api/workspaces/:ws/auth-profiles", () => HttpResponse.json(PROFILE_FIXTURE)),
      http.post("/api/scan", async ({ request }) => {
        posted = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ workspace: "ws1", scan_id: "s1" });
      }),
    );
    renderPage();
    clickTab("黑盒");
    await selectWorkspace("ws1");
    // wbScans 已 fixture → 等待 reuse 自动选最新（说明 listScans 已返回，ws 已选定）
    await waitFor(() => expect(screen.getByText(/ws1-foo-20260731-1200/)).toBeInTheDocument());
    // 黑盒 url 必填
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
    // 启用登录（点「配置登录」展开即开启——对齐 preview）
    fireEvent.click(screen.getByRole("button", { name: /配置登录/ }));
    await expectAuthEnabled();
    // 切到 profile（来源 segmented button「使用档案」）
    fireEvent.click(screen.getByRole("button", { name: /使用档案/ }));
    // 等档案卡出现（左列卡片 button，accessible name 以档案名 NG 开头）→ 选 NG
    await waitFor(() => expect(screen.getByRole("button", { name: /^NG/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /^NG/ }));
    // 选档案后角色默认全选（角色行 button name 含 "role · username"，已选中态）
    await waitFor(() => expect(screen.getByRole("button", { name: /admin · a/ })).toBeInTheDocument());
    // 默认全选 → 提交应 enabled（无需再点角色行）
    await waitFor(() => expect(screen.getByRole("button", { name: /开始渗透/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /开始渗透/ }));
    await waitFor(() => expect(posted).toBeDefined());
    expect(posted!.auth_profile_id).toBe("prof_1");
    expect(posted!.auth_credential_ids).toEqual(["cred_a"]); // 默认全选
    // 与 inline 互斥——profile 模式不发 authentication
    expect(posted!.authentication).toBeUndefined();
    // reuse 白盒 scan_id 仍带
    expect(posted!.reuse_whitebox_scan_id).toBe("20260731-1200");
  });

  // profile 模式：取消全选后无角色 → 校验拦空，提交 disabled。
  it("profile 模式取消全选角色 → 提交 disabled（validateAuth 拦空）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json(WB_SCANS)),
      http.get("/api/workspaces/:ws/auth-profiles", () => HttpResponse.json(PROFILE_FIXTURE)),
    );
    renderPage();
    clickTab("黑盒");
    await selectWorkspace("ws1");
    await waitFor(() => expect(screen.getByText(/ws1-foo-20260731-1200/)).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /配置登录/ }));
    await expectAuthEnabled();
    fireEvent.click(screen.getByRole("button", { name: /使用档案/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /^NG/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /^NG/ }));
    // 选档案后角色默认全选 → 提交 enabled
    await waitFor(() => expect(screen.getByRole("button", { name: /开始渗透/ })).toBeEnabled());
    // 点角色行取消选中（toggle off）→ 无角色 → 提交 disabled
    fireEvent.click(screen.getByRole("button", { name: /admin · a/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /开始渗透/ })).toBeDisabled());
  });

  // 多角色子集：选 2 角色档案 → 默认全选 → 取消 1 个 → 提交发子集（1 个 id）。
  it("profile 模式多角色：取消一个 → 发 auth_credential_ids 子集", async () => {
    let posted: Record<string, unknown> | undefined;
    const MULTI = [{
      id: "prof_m", name: "multi", login_url: "http://t/", login_type: "form",
      credentials: [
        { id: "cred_admin", role: "admin", username: "adm", verify_status: { state: "unverified" as const } },
        { id: "cred_user", role: "user", username: "usr", verify_status: { state: "unverified" as const } },
      ],
    }];
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json(WB_SCANS)),
      http.get("/api/workspaces/:ws/auth-profiles", () => HttpResponse.json(MULTI)),
      http.post("/api/scan", async ({ request }) => {
        posted = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ workspace: "ws1", scan_id: "s1" });
      }),
    );
    renderPage();
    clickTab("黑盒");
    await selectWorkspace("ws1");
    await waitFor(() => expect(screen.getByText(/ws1-foo-20260731-1200/)).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /配置登录/ }));
    await expectAuthEnabled();
    fireEvent.click(screen.getByRole("button", { name: /使用档案/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /^multi/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /^multi/ }));
    // 默认全选 2 个 → 取消 admin（剩 user）
    await waitFor(() => expect(screen.getByRole("button", { name: /admin · adm/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /admin · adm/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /开始渗透/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /开始渗透/ }));
    await waitFor(() => expect(posted).toBeDefined());
    expect(posted!.auth_profile_id).toBe("prof_m");
    expect(posted!.auth_credential_ids).toEqual(["cred_user"]); // 子集：仅 user
  });

  // inline 模式（默认）打开 auth 后仍能正常展开既有字段——回归保底（不破坏旧流程）。
  it("inline 模式（默认 source）：启用后显既有 login_url 输入（不破坏旧流程）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json(WB_SCANS)),
    );
    renderPage();
    clickTab("黑盒");
    await selectWorkspace("ws1");
    await waitFor(() => expect(screen.getByText(/ws1-foo-20260731-1200/)).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /配置登录/ }));
    await expectAuthEnabled();
    // 默认 source=inline → 下方块显既有 inline 字段（login_url placeholder https://example.com/login）
    expect(screen.getByPlaceholderText("https://example.com/login")).toBeInTheDocument();
    // inline 模式不显 ProfilePicker 的「选择档案」标题
    expect(screen.queryByText("选择档案")).toBeNull();
  });

  // inline 模式（2026-08-06 重排）：展开后下方横向铺开「登录入口」+「凭据」两卡，
  // 右栏显「登录步骤」+「存为档案」（字段不再纵向堆叠撑高，一屏装下）。
  it("inline 模式：下方横向铺开（显「登录入口」eyebrow + 右栏登录步骤）", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json(WB_SCANS)));
    renderPage();
    clickTab("黑盒");
    await selectWorkspace("ws1");
    await waitFor(() => expect(screen.getByText(/ws1-foo-20260731-1200/)).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /配置登录/ }));
    await expectAuthEnabled();
    // inline 模式（默认 source）-> 下方块标题 + 凭据卡 eyebrow 都含「登录入口」（getAllByText 多处命中）
    expect(screen.getAllByText("登录入口").length).toBeGreaterThan(0);
    // 既有 inline 字段仍在（loginUrl placeholder）
    expect(screen.getByPlaceholderText("https://example.com/login")).toBeInTheDocument();
    // 右栏显登录步骤 label（inline 模式右栏增强）
    expect(screen.getByText(/登录步骤/)).toBeInTheDocument();
  });
});

// === inline 临时填写 -> 保存为认证档案（2026-08-06：保存入口在右栏，始终一行 Input+Button）===
// 用户意图：临时填写区既能直接运行（开始渗透），也能保存成档案复用；保存入口在右栏与登录步骤同处。
describe("ScanNewPage 黑盒 inline 保存为认证档案", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  // Label 与 Input 同处 space-y-1 div（BottomInlineBlock 凭据区结构）；Label 无 htmlFor，按文本定位 input。
  function inputByLabel(labelText: RegExp | string) {
    const label = screen.getByText(labelText);
    return label.parentElement?.querySelector("input") as HTMLInputElement;
  }

  // 通用：黑盒 + 选 ws1 + 填 url + 启用登录（inline 默认）+ 填 loginUrl/role/username/password
  async function fillInlineAuth() {
    renderPage();
    clickTab("黑盒");
    await selectWorkspace("ws1");
    await waitFor(() => expect(screen.getByText(/ws1-foo-20260731-1200/)).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
    // 认证默认折叠——点「配置登录」展开即开启
    fireEvent.click(screen.getByRole("button", { name: /配置登录/ }));
    await expectAuthEnabled();
    fireEvent.change(screen.getByPlaceholderText("https://example.com/login"), { target: { value: "http://t/login" } });
    fireEvent.change(inputByLabel("角色"), { target: { value: "admin" } });
    fireEvent.change(inputByLabel("用户名"), { target: { value: "alice" } });
    fireEvent.change(inputByLabel("密码"), { target: { value: "pw" } });
  }

  it("保存：body 含 login_url/role + 自动切 profile 选中新建档案", async () => {
    let created: Record<string, unknown> | undefined;
    const NEW_PROFILE = {
      id: "prof_new", name: "NG 后台", login_url: "http://t/login", login_type: "form",
      credentials: [{ id: "cred_new", role: "admin", username: "alice", verify_status: { state: "unverified" } }],
    };
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json(WB_SCANS)),
      http.post("/api/workspaces/:ws/auth-profiles", async ({ request }) => {
        created = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(NEW_PROFILE);
      }),
      // 保存后 BottomProfileBlock mount + refreshSignal 触发重拉 -> 返回含新建档案的列表
      http.get("/api/workspaces/:ws/auth-profiles", () => HttpResponse.json([NEW_PROFILE])),
    );
    await fillInlineAuth();
    // 右栏「存为档案」一行：填档案名（loginUrl+username 已填 -> 保存按钮可点），角色取凭据区值
    const nameInput = screen.getByPlaceholderText(/NG 管理后台/);
    fireEvent.change(nameInput, { target: { value: "NG 后台" } });
    fireEvent.click(screen.getByRole("button", { name: "保存为认证档案" }));
    await waitFor(() => expect(created).toBeDefined());
    // body 断言：字段一一映射，role 取凭据区填写值
    expect(created!.name).toBe("NG 后台");
    expect(created!.login_url).toBe("http://t/login");
    expect(created!.login_type).toBe("form");
    const creds = created!.credentials as Record<string, unknown>[];
    expect(creds[0].username).toBe("alice");
    expect(creds[0].password).toBe("pw");
    expect(creds[0].role).toBe("admin");
    // 自动切 profile 模式 + 选中：档案卡 + 右栏摘要都显档案名（getAllByText），角色行显 "admin · alice"
    await waitFor(() => expect(screen.getAllByText("NG 后台").length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByRole("button", { name: /admin · alice/ })).toBeInTheDocument());
  });

  it("保存：档案名重复 422 -> toast 错误，保留输入可重试", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json(WB_SCANS)),
      http.post("/api/workspaces/:ws/auth-profiles", () =>
        HttpResponse.json({ detail: "档案名已存在" }, { status: 422 })),
    );
    const spy = vi.spyOn(toast, "error");
    await fillInlineAuth();
    const nameInput = screen.getByPlaceholderText(/NG 管理后台/);
    fireEvent.change(nameInput, { target: { value: "dup" } });
    fireEvent.click(screen.getByRole("button", { name: "保存为认证档案" }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    // 仍保留档案名输入框（可改名重试）
    expect(screen.getByPlaceholderText(/NG 管理后台/)).toBeInTheDocument();
  });

  it("保存：未填登录地址/用户名 -> 保存按钮 disabled + 常驻提示（不发请求）", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json(WB_SCANS)));
    renderPage();
    clickTab("黑盒");
    await selectWorkspace("ws1");
    await waitFor(() => expect(screen.getByText(/ws1-foo-20260731-1200/)).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /配置登录/ }));
    await expectAuthEnabled();
    // 未填 loginUrl/username -> 保存按钮 disabled + 显「请先填写登录地址和用户名」提示
    const saveBtn = screen.getByRole("button", { name: "保存为认证档案" });
    expect(saveBtn).toBeDisabled();
    expect(screen.getByText(/请先填写登录地址和用户名/)).toBeInTheDocument();
  });
});

// === #1 单一 disclosure（展开即启用）：展开即开、收起即关（停用但留草稿）；冗余「需要登录」Switch 移除 ===
describe("ScanNewPage 黑盒认证区单一 disclosure（展开即启用）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  // inline 凭据区按 label 定位 input（Label + Input 同处 space-y-1 div，Label 无 htmlFor）。
  function inputByLabel(labelText: RegExp | string) {
    const label = screen.getByText(labelText);
    return label.parentElement?.querySelector("input") as HTMLInputElement;
  }

  // 通用前置：黑盒 + 选 ws1 + 填 url + 等 reuse 候选就绪。
  async function setupBlackbox() {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json(WB_SCANS)));
    renderPage();
    clickTab("黑盒");
    await selectWorkspace("ws1");
    await waitFor(() => expect(screen.getByText(/ws1-foo-20260731-1200/)).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
  }

  it("收起 = 停用但留草稿：状态回「未登录」、配置块隐藏、再展开 loginUrl 仍在", async () => {
    await setupBlackbox();
    // 展开 + 填 loginUrl（产生草稿）
    fireEvent.click(screen.getByRole("button", { name: /配置登录/ }));
    await expectAuthEnabled();
    fireEvent.change(screen.getByPlaceholderText("https://example.com/login"), { target: { value: "http://t/login" } });
    // 收起 → enabled=false：状态回未登录、凭据块隐藏
    fireEvent.click(screen.getByRole("button", { name: /收起/ }));
    await waitFor(() => expect(screen.getByText(/未登录状态扫描/)).toBeInTheDocument());
    expect(screen.queryByPlaceholderText("https://example.com/login")).toBeNull();
    // 再展开 → 草稿恢复（loginUrl 未丢）
    fireEvent.click(screen.getByRole("button", { name: /配置登录/ }));
    await expectAuthEnabled();
    expect(screen.getByDisplayValue("http://t/login")).toBeInTheDocument();
  });

  it("收起后提交不发 authentication（buildBody 受 enabled 控制，不因曾配置而发）", async () => {
    let posted: Record<string, unknown> | undefined;
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json(WB_SCANS)),
      http.post("/api/scan", async ({ request }) => {
        posted = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ workspace: "ws1", scan_id: "s1" });
      }),
    );
    renderPage();
    clickTab("黑盒");
    await selectWorkspace("ws1");
    await waitFor(() => expect(screen.getByText(/ws1-foo-20260731-1200/)).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
    // 展开 + 填有效 inline 凭据（loginUrl + username 使 validateAuth 通过——否则提交 disabled 测不到「发了」）
    fireEvent.click(screen.getByRole("button", { name: /配置登录/ }));
    await expectAuthEnabled();
    fireEvent.change(screen.getByPlaceholderText("https://example.com/login"), { target: { value: "http://t/login" } });
    fireEvent.change(inputByLabel("用户名"), { target: { value: "alice" } });
    // 收起（停用）→ 提交：enabled=false 故 buildBody 不带 auth
    fireEvent.click(screen.getByRole("button", { name: /收起/ }));
    fireEvent.click(screen.getByRole("button", { name: /开始渗透/ }));
    await waitFor(() => expect(posted).toBeDefined());
    expect(posted!.authentication).toBeUndefined();
    expect(posted!.auth_profile_id).toBeUndefined();
  });

  it("草稿存在时收起态按钮显「已配置」标记（折叠不丢配置的可见信号）", async () => {
    await setupBlackbox();
    fireEvent.click(screen.getByRole("button", { name: /配置登录/ }));
    await expectAuthEnabled();
    fireEvent.change(screen.getByPlaceholderText("https://example.com/login"), { target: { value: "http://t/login" } });
    // 收起 → 草稿仍在 → 按钮显「已配置」标记
    fireEvent.click(screen.getByRole("button", { name: /收起/ }));
    expect(screen.getByRole("button", { name: /已配置/ })).toBeInTheDocument();
  });

  it("「需要登录」冗余开关已移除（展开即启用，单一 disclosure）", async () => {
    await setupBlackbox();
    fireEvent.click(screen.getByRole("button", { name: /配置登录/ }));
    await expectAuthEnabled();
    // 不再有独立「需要登录」Switch
    expect(screen.queryByRole("switch")).toBeNull();
  });
});

// === #2 inline 多角色：主账号（authentication）+ 附加角色（auth_accounts），多身份对比 ===
describe("ScanNewPage 黑盒 inline 多角色（#2 附加角色 → auth_accounts）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  // 主账号字段（BottomInlineBlock 内 Label 无 htmlFor）按文本定位 input。
  function inputByLabel(labelText: RegExp | string) {
    const label = screen.getByText(labelText);
    return label.parentElement?.querySelector("input") as HTMLInputElement;
  }

  it("添加附加角色 → 提交发 auth_accounts（附加）+ authentication（主账号）", async () => {
    let posted: Record<string, unknown> | undefined;
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json(WB_SCANS)),
      http.post("/api/scan", async ({ request }) => {
        posted = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ workspace: "ws1", scan_id: "s1" });
      }),
    );
    renderPage();
    clickTab("黑盒");
    await selectWorkspace("ws1");
    await waitFor(() => expect(screen.getByText(/ws1-foo-20260731-1200/)).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /配置登录/ }));
    await expectAuthEnabled();
    // 主账号（add 之前只有主账号「用户名」标签，inputByLabel 安全不歧义）
    fireEvent.change(screen.getByPlaceholderText("https://example.com/login"), { target: { value: "http://t/login" } });
    fireEvent.change(inputByLabel("用户名"), { target: { value: "admin" } });
    fireEvent.change(inputByLabel("密码"), { target: { value: "pw" } });
    // 添加附加角色 → CredentialRows 多一行（primary 占 [0]，附加角色 [1]）
    fireEvent.click(screen.getByRole("button", { name: /添加角色/ }));
    fireEvent.change(screen.getAllByLabelText("角色")[1], { target: { value: "user" } });
    fireEvent.change(screen.getAllByLabelText("用户名")[1], { target: { value: "bob" } });
    fireEvent.change(screen.getAllByLabelText("密码")[1], { target: { value: "bobpw" } });
    fireEvent.click(screen.getByRole("button", { name: /开始渗透/ }));
    await waitFor(() => expect(posted).toBeDefined());
    const p = posted as { authentication: { credentials: { username: string } }; auth_accounts?: { role: string; username: string; password: string }[] };
    expect(p.authentication.credentials.username).toBe("admin");  // 主账号
    expect(p.auth_accounts).toEqual([
      expect.objectContaining({ role: "user", username: "bob", password: "bobpw" }),
    ]);
  });

  it("未添加附加角色 → 提交不发 auth_accounts（单角色向后兼容）", async () => {
    let posted: Record<string, unknown> | undefined;
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json(WB_SCANS)),
      http.post("/api/scan", async ({ request }) => {
        posted = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ workspace: "ws1", scan_id: "s1" });
      }),
    );
    renderPage();
    clickTab("黑盒");
    await selectWorkspace("ws1");
    await waitFor(() => expect(screen.getByText(/ws1-foo-20260731-1200/)).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /配置登录/ }));
    await expectAuthEnabled();
    fireEvent.change(screen.getByPlaceholderText("https://example.com/login"), { target: { value: "http://t/login" } });
    fireEvent.change(screen.getByText("用户名").parentElement!.querySelector("input")!, { target: { value: "admin" } });
    fireEvent.change(screen.getByText("密码").parentElement!.querySelector("input")!, { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: /开始渗透/ }));
    await waitFor(() => expect(posted).toBeDefined());
    expect((posted as Record<string, unknown>).auth_accounts).toBeUndefined();
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
    expect(screen.getByRole("tab", { name: "白盒" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(screen.getByText("ws1")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("foo")).toBeInTheDocument());
  });

  it("黑盒：state -> tab 黑盒 + url/reuse/auth 预填", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json(WB_SCANS)));
    const auth = {
      login_type: "form", login_url: "http://t.example/login",
      credentials: { username: "admin", password: "pw" },
    };
    renderPage("/scan/new", { type: "blackbox", workspace: "ws1", url: "http://t.example",
      reuseScanId: "20260731-1200", auth });
    expect(screen.getByRole("tab", { name: "黑盒" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(screen.getByText("ws1")).toBeInTheDocument());
    expect(screen.getByDisplayValue("http://t.example")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/ws1-foo-20260731-1200/)).toBeInTheDocument());
    // auth 预填（enabled=true -> 登录配置展开，login_url/username 已填）
    expect(screen.getByDisplayValue("http://t.example/login")).toBeInTheDocument();
    // username 预填 admin（角色字段默认也是 admin，故 getAllByDisplayValue 多处命中）
    expect(screen.getAllByDisplayValue("admin").length).toBeGreaterThan(0);
  });

  it("黑盒 reuseScanId 预填保留：多条候选时选中预填的而非默认最新", async () => {
    const wbScans = [
      { scan_id: "wb-new", scan_type: "whitebox", status: "completed", created_at: 9999, vuln_count: 0, is_running: false, workflow_id: "ws1-wb-new" },
      { scan_id: "wb-old", scan_type: "whitebox", status: "completed", created_at: 1111, vuln_count: 0, is_running: false, workflow_id: "ws1-wb-old" },
    ];
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json(wbScans)));
    renderPage("/scan/new", { type: "blackbox", workspace: "ws1", reuseScanId: "wb-old" });
    await waitFor(() => expect(screen.getByText("ws1")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/ws1-wb-old/)).toBeInTheDocument());
    // 预填 wb-old 保留，未被「默认选最新 wb-new」覆盖
    expect(screen.queryByText(/ws1-wb-new/)).not.toBeInTheDocument();
  });
});

describe("ScanNewPage 配色 · coral 收窄到点缀（对齐全站克制基调）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("右侧信息侧栏已移除（白盒无「审计范围」，黑盒无「攻击面」）", () => {
    renderPage();
    expect(screen.queryByText("审计范围")).toBeNull();
    clickTab("黑盒");
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

  it("presetToAuthState: 空 preset → DEFAULT_AUTH（disabled, inline）", () => {
    const state = presetToAuthState({});
    expect(state.enabled).toBe(false);
    expect(state.source).toBe("inline");
  });
});
