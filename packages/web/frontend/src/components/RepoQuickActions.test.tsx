/** RepoQuickActions（2026-09-03 仓库入口整合 C 段）：扫描表单选中仓库后的快捷
 *  操作条——当前分支切换（checkout）+ 更新（pull），免去跑去仓库页。
 *  linked 只读不渲染；upload 无 pull（静态快照）但保留本地分支切换。 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { SWRConfig } from "swr";
import { RepoQuickActions } from "./RepoQuickActions";

vi.mock("react-i18next", () => {
  const t = (k: string) => k;
  return { useTranslation: () => ({ t }) };
});
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
import { toast } from "sonner";

let fetchCalls: Array<{ url: string; method: string; body?: unknown }> = [];

function mockFetch(responses: Record<string, { status: number; body: unknown }>) {
  vi.spyOn(window, "fetch").mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    fetchCalls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    for (const [prefix, resp] of Object.entries(responses)) {
      if (url.includes(prefix)) return new Response(JSON.stringify(resp.body), { status: resp.status });
    }
    return new Response("{}", { status: 404 });
  });
}

const GIT_REPO = {
  name: "nodegoat", state: "ready", linked: false,
  source: { kind: "git", url: "https://gitlab.example/nodegoat.git", branch: "main" },
};

function renderActions(repo: typeof GIT_REPO, ws = "ws1") {
  return render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <RepoQuickActions workspace={ws} repo={repo as never} />
    </SWRConfig>,
  );
}

beforeEach(() => { fetchCalls = []; vi.mocked(toast.success).mockClear(); vi.mocked(toast.error).mockClear(); });
afterEach(() => { vi.restoreAllMocks(); cleanup(); });

describe("RepoQuickActions", () => {
  it("ready git 仓库：渲染当前分支（BranchCombobox）+ 更新按钮；点更新 → POST pull → toast + 延迟刷新 repos", async () => {
    mockFetch({
      "/repos/nodegoat/pull": { status: 202, body: { pulling: "nodegoat" } },
      "/repos": { status: 200, body: [] },
    });
    renderActions(GIT_REPO);
    expect(screen.getByText("main")).toBeTruthy();
    const btn = screen.getByTestId("repo-pull-btn");
    fireEvent.click(btn);
    await waitFor(() => {
      const pull = fetchCalls.find((c) => c.url.includes("/repos/nodegoat/pull"));
      expect(pull?.method).toBe("POST");
    });
    expect(toast.success).toHaveBeenCalled();
    // 1.5s 延迟刷新（对齐 ReposTab PULL_REFRESH_DELAY_MS）——repos 重新拉取
    await waitFor(() => {
      expect(fetchCalls.filter((c) => c.url.includes("/api/workspaces/ws1/repos")).length).toBeGreaterThan(0);
    }, { timeout: 4000 });
  });

  it("切分支 → POST checkout → 成功 toast + repos 立即刷新", async () => {
    mockFetch({
      "/repos/nodegoat/branches": { status: 200, body: { branches: ["dev", "main"] } },
      "/repos/nodegoat/checkout": { status: 200, body: { checked_out: "dev" } },
      "/repos": { status: 200, body: [] },
    });
    renderActions(GIT_REPO);
    fireEvent.click(screen.getByLabelText("repoDetail.switchAria"));
    const dev = await screen.findByText("dev");
    fireEvent.click(dev);
    await waitFor(() => {
      const co = fetchCalls.find((c) => c.url.includes("/repos/nodegoat/checkout"));
      expect(co?.method).toBe("POST");
      expect(co?.body).toEqual({ branch: "dev" });
    });
    expect(toast.success).toHaveBeenCalled();
    await waitFor(() => {
      expect(fetchCalls.some((c) => c.url.endsWith("/api/workspaces/ws1/repos"))).toBe(true);
    });
  });

  it("checkout 409（被扫描引用）→ toast 错误", async () => {
    mockFetch({
      "/repos/nodegoat/branches": { status: 200, body: { branches: ["dev", "main"] } },
      "/repos/nodegoat/checkout": { status: 409, body: { detail: "仓库正被扫描引用" } },
      "/repos": { status: 200, body: [] },
    });
    renderActions(GIT_REPO);
    fireEvent.click(screen.getByLabelText("repoDetail.switchAria"));
    fireEvent.click(await screen.findByText("dev"));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
  });

  it("upload 仓库：无更新按钮（静态快照不可 pull），保留分支切换", () => {
    mockFetch({});
    renderActions({
      ...GIT_REPO,
      source: { kind: "upload", url: "", branch: "main" },
    });
    expect(screen.queryByTestId("repo-pull-btn")).toBeNull();
    expect(screen.getByText("main")).toBeTruthy(); // 分支切换仍在
  });

  it("linked 仓库：不渲染任何操作（只读共享路径）", () => {
    mockFetch({});
    const { container } = renderActions({ ...GIT_REPO, linked: true });
    expect(screen.queryByTestId("repo-quick-actions")).toBeNull();
    expect(container.textContent).toBe("");
  });
});
