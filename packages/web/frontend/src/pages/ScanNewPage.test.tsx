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

// === auth-profile-vault Task 14：profile 模式（选档案+角色，buildBody 发 auth_profile_id+auth_credential_id） ===
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

  // 黑盒 profile 模式：选档案+角色 → buildBody 发 auth_profile_id+auth_credential_id，无 authentication。
  it("profile 模式发 auth_profile_id + auth_credential_id（无 authentication）", async () => {
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
    // 启用登录（Step4 顶部 Switch）
    fireEvent.click(screen.getByRole("switch"));
    await waitFor(() => expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("true"));
    // 默认 source=inline → 切到 profile（source Select trigger 显「临时填写」）
    await selectOption("临时填写", "使用档案");
    // ProfilePicker mount → 等档案 Select 出现（trigger 显 placeholder「选择认证档案」）
    await waitFor(() => expect(screen.getByText("选择认证档案")).toBeInTheDocument());
    // 选档案 NG（trigger 显 placeholder「选择认证档案」；NG 是 listAuthProfiles 返回的 profile.name）
    await selectOption("选择认证档案", "NG");
    // 选档案后角色 Select 出现（trigger 显 placeholder「选择登录角色」）
    await waitFor(() => expect(screen.getByText("选择登录角色")).toBeInTheDocument());
    await selectOption("选择登录角色", /admin · a/);
    // 提交应 enabled → 点提交
    await waitFor(() => expect(screen.getByRole("button", { name: /开始渗透/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /开始渗透/ }));
    await waitFor(() => expect(posted).toBeDefined());
    expect(posted!.auth_profile_id).toBe("prof_1");
    expect(posted!.auth_credential_id).toBe("cred_a");
    // 与 inline 互斥——profile 模式不发 authentication
    expect(posted!.authentication).toBeUndefined();
    // reuse 白盒 scan_id 仍带
    expect(posted!.reuse_whitebox_scan_id).toBe("20260731-1200");
  });

  // profile 模式但未选角色 → 校验拦空，提交 disabled。
  it("profile 模式选档案未选角色 → 提交 disabled（validateAuth 拦空）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json(WB_SCANS)),
      http.get("/api/workspaces/:ws/auth-profiles", () => HttpResponse.json(PROFILE_FIXTURE)),
    );
    renderPage();
    clickTab("黑盒");
    await selectWorkspace("ws1");
    await waitFor(() => expect(screen.getByText(/ws1-foo-20260731-1200/)).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
    fireEvent.click(screen.getByRole("switch"));
    await waitFor(() => expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("true"));
    await selectOption("临时填写", "使用档案");
    await waitFor(() => expect(screen.getByText("选择认证档案")).toBeInTheDocument());
    await selectOption("选择认证档案", "NG");
    // 选了档案但未选角色 → 提交仍 disabled（validateAuth 返 selectCredential）
    expect(screen.getByRole("button", { name: /开始渗透/ })).toBeDisabled();
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
    fireEvent.click(screen.getByRole("switch"));
    await waitFor(() => expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("true"));
    // 默认 source=inline → 显既有 inline 字段（login_url placeholder https://example.com/login）
    expect(screen.getByPlaceholderText("https://example.com/login")).toBeInTheDocument();
    // 不显 ProfilePicker 的「选择认证档案」placeholder
    expect(screen.queryByText("选择认证档案")).toBeNull();
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
    expect(screen.getByDisplayValue("admin")).toBeInTheDocument();
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
    enabled: true, source: "inline", profileId: "", credentialId: "",
    loginType: "form", loginUrl: "https://x/login", username: "admin",
    password: "pw", totpSecret: "T", emailLoginEnabled: false, emailAddress: "",
    emailPassword: "", emailTotp: "", loginFlow: "a\nb",
  };
  // t 只回 key（断言用 key 本身，不依赖 i18n 文案）
  const t = ((k: string) => k) as never;

  it("buildAuthPayload → ScanAuthentication（snake_case + login_flow 按行 split）", () => {
    const p = buildAuthPayload({ ...base });
    expect(p.login_type).toBe("form");
    expect(p.login_url).toBe("https://x/login");
    expect(p.credentials).toEqual({ username: "admin", password: "pw", totp_secret: "T" });
    expect(p.login_flow).toEqual(["a", "b"]);
  });

  it("loginFlow 全空行 → 不发 login_flow 字段", () => {
    const p = buildAuthPayload({ ...base, loginFlow: "  \n \n" });
    expect(p.login_flow).toBeUndefined();
  });

  it("emailLogin 启用 → credentials.email_login 含 address/password/totp_secret", () => {
    const p = buildAuthPayload({ ...base, emailLoginEnabled: true, emailAddress: "a@b", emailPassword: "ep", emailTotp: "et" });
    expect(p.credentials.email_login).toEqual({ address: "a@b", password: "ep", totp_secret: "et" });
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

  // === auth-profile-vault Task 14：profile 模式校验 ===
  it("validateAuth: profile 模式缺 profileId → authProfileRequired", () => {
    expect(validateAuth({ ...base, source: "profile", profileId: "", credentialId: "" }, t))
      .toBe("scan.errors.authProfileRequired");
  });

  it("validateAuth: profile 模式有 profileId 缺 credentialId → authCredentialRequired", () => {
    expect(validateAuth({ ...base, source: "profile", profileId: "p1", credentialId: "" }, t))
      .toBe("scan.errors.authCredentialRequired");
  });

  it("validateAuth: profile 模式 profileId+credentialId 齐 → null", () => {
    expect(validateAuth({ ...base, source: "profile", profileId: "p1", credentialId: "c1" }, t))
      .toBeNull();
  });

  it("presetToAuthState: authProfileId 非空 → source=profile + ids（auth 不读）", () => {
    const state = presetToAuthState({
      authProfileId: "p1", authCredentialId: "c1",
      // 故意同时给 auth:inline——profile 优先，应忽略
      auth: { login_type: "form", login_url: "http://x", credentials: { username: "u" } },
    });
    expect(state.enabled).toBe(true);
    expect(state.source).toBe("profile");
    expect(state.profileId).toBe("p1");
    expect(state.credentialId).toBe("c1");
  });

  it("presetToAuthState: 仅 auth（inline）→ source=inline + authFromPayload", () => {
    const state = presetToAuthState({
      auth: { login_type: "form", login_url: "http://x/l", credentials: { username: "u", password: "p" } },
    });
    expect(state.enabled).toBe(true);
    expect(state.source).toBe("inline");
    expect(state.loginUrl).toBe("http://x/l");
    expect(state.username).toBe("u");
  });

  it("presetToAuthState: 空 preset → DEFAULT_AUTH（disabled, inline）", () => {
    const state = presetToAuthState({});
    expect(state.enabled).toBe(false);
    expect(state.source).toBe("inline");
  });
});
