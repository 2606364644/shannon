// AuthProfileTestPage: 档案级多选角色测试页——默认全选 + 计数 + toggle + 全选切换 + 发起 test-batch +
// 重载恢复 running（订阅 VerifyLivePanel）。回看面板依赖 react-window,jsdom 只验"不崩 + 被触发"。
import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import { AuthProfileTestPage, hostToParams } from "./AuthProfileTestPage";
import type { AuthProfile } from "@/api/types";
import type { HostFormState } from "./ScanNewPage";

// 恢复/发起后订阅 VerifyLivePanel → useEventSource → new EventSource；jsdom 无原生 EventSource，
// mock 返空流（只验"识别 running → 进 live 态/发起成功"，不验 SSE 事件本身）。
vi.mock("@/api/useEventSource", () => ({
  useEventSource: () => ({ events: [], status: "open" as const, lastEventId: undefined }),
}));

const prof: AuthProfile = {
  id: "prof_1", name: "NG", login_url: "http://t/", login_type: "form",
  credentials: [
    { id: "c1", role: "admin", username: "admin", verify_status: { state: "unverified" } },
    { id: "c2", role: "user", username: "u1", verify_status: { state: "unverified" } },
    { id: "c3", role: "guest", username: "g1", verify_status: { state: "unverified" } },
  ],
};
const runningProf: AuthProfile = {
  id: "prof_1", name: "NG", login_url: "http://t/", login_type: "form",
  credentials: [
    { id: "c1", role: "admin", username: "admin",
      verify_status: { state: "running", workflow_id: "authval-batch-ws1-running1", probe_dir: "/p/probe-r1" } },
    { id: "c2", role: "user", username: "u1", verify_status: { state: "unverified" } },
    { id: "c3", role: "guest", username: "g1", verify_status: { state: "unverified" } },
  ],
};

let currentProf: AuthProfile;
let batchCalls = 0;
let batchBody: unknown;
const server = setupServer(
  http.get("/api/workspaces/:ws/auth-profiles/:pid", () => HttpResponse.json(currentProf)),
  http.post("/api/workspaces/:ws/auth-profiles/:pid/test-batch", async ({ request }) => {
    batchCalls++;
    batchBody = await request.json();
    return HttpResponse.json({ workflow_id: "authval-batch-ws1-abc" });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => { i18n.changeLanguage("zh"); batchCalls = 0; batchBody = undefined; });
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/p/ws1/auth-profiles/prof_1"]}>
      <Routes>
        <Route path="/p/:workspace/auth-profiles/:pid" element={<AuthProfileTestPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("hostToParams", () => {
  const base: HostFormState = { enabled: false, mode: "profile", profileId: "", hostUrl: "" };
  it("未启用 → 空（直连）", () => {
    expect(hostToParams(base)).toEqual({});
  });
  it("profile 模式 → hostProfileId", () => {
    expect(hostToParams({ ...base, enabled: true, mode: "profile", profileId: "host_p1" }))
      .toEqual({ hostProfileId: "host_p1" });
  });
  it("url 模式 → hostUrl", () => {
    expect(hostToParams({ ...base, enabled: true, mode: "url", hostUrl: "https://h.test/get" }))
      .toEqual({ hostUrl: "https://h.test/get" });
  });
});

describe("AuthProfileTestPage", () => {
  it("加载档案: 默认全选 + 计数 3/3 + 角色列表 + 开始按钮", async () => {
    currentProf = prof;
    renderPage();
    await waitFor(() => expect(screen.getByText("NG")).toBeInTheDocument());
    expect(screen.getAllByText(/admin/).length).toBeGreaterThan(0);
    expect(screen.getByText(/3\/3/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始测试选中角色" })).toBeInTheDocument();
    // 3 个 toggle 全选中（aria-pressed=true）
    expect(screen.getAllByRole("button", { pressed: true })).toHaveLength(3);
  });

  it("取消一个角色 → 计数 2/3", async () => {
    currentProf = prof;
    renderPage();
    await waitFor(() => expect(screen.getByText(/3\/3/)).toBeInTheDocument());
    const toggles = screen.getAllByRole("button", { pressed: true });
    fireEvent.click(toggles[0]);  // 取消第一个角色
    await waitFor(() => expect(screen.getByText(/2\/3/)).toBeInTheDocument());
    expect(screen.getAllByRole("button", { pressed: true })).toHaveLength(2);
  });

  it("取消全选 → 0 选 + 开始按钮 disabled; 再全选 → 3 选", async () => {
    currentProf = prof;
    renderPage();
    await waitFor(() => expect(screen.getByText(/3\/3/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "取消全选" }));
    await waitFor(() => expect(screen.getByText(/0\/3/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "开始测试选中角色" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "全选" }));
    await waitFor(() => expect(screen.getByText(/3\/3/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "开始测试选中角色" })).not.toBeDisabled();
  });

  it("发起测试 → POST test-batch（全选省略 cred_ids）", async () => {
    currentProf = prof;
    renderPage();
    await waitFor(() => expect(screen.getByText(/3\/3/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "开始测试选中角色" }));
    await waitFor(() => expect(batchCalls).toBe(1));
    // 全选 → cred_ids 省略（body 不含 cred_ids 键 → JSON {}）
    expect(batchBody).toEqual({});
  });

  it("发起测试（子集）→ POST test-batch 带 cred_ids", async () => {
    currentProf = prof;
    renderPage();
    await waitFor(() => expect(screen.getByText(/3\/3/)).toBeInTheDocument());
    // 取消第二个（c2）
    const toggles = screen.getAllByRole("button", { pressed: true });
    fireEvent.click(toggles[1]);
    await waitFor(() => expect(screen.getByText(/2\/3/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "开始测试选中角色" }));
    await waitFor(() => expect(batchCalls).toBe(1));
    expect(batchBody).toEqual({ cred_ids: ["c1", "c3"] });  // c2 被取消
  });

  it("重载发现 running cred → 恢复（按钮显示测试中、不落空态）", async () => {
    currentProf = runningProf;
    renderPage();
    await waitFor(() => expect(screen.getByText("NG")).toBeInTheDocument());
    // 恢复 effect 识别 running → setPolling + setTesting → 按钮变"测试中…"并 disabled。
    // 核心：重载页面发现批次进行中不落空态、自动恢复轮询。
    await waitFor(() => expect(screen.getByRole("button", { name: /测试中/ })).toBeDisabled());
  });

  it("渲染 HOST 解析入口（复用黑盒 HOST 能力：选 HOST 走代理、不选直连）", async () => {
    currentProf = prof;
    renderPage();
    await waitFor(() => expect(screen.getByText("NG")).toBeInTheDocument());
    expect(screen.getByText("HOST 解析")).toBeInTheDocument();
  });
});
