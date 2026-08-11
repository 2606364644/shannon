// VerifyProcessPage: 认证过程页(新标签打开)——header 信息 + 测试登录按钮 + 回看触发。
// 回看面板(DashboardPanel/LogStream)渲染依赖 react-window 虚拟列表, jsdom 下只验"不崩 + 被触发",
// 不验像素布局(对齐 LogStream 注释约定)。核心断言落在页面自渲染的 header/状态/按钮上。
import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import { VerifyProcessPage } from "./VerifyProcessPage";
import type { AuthProfile } from "@/api/types";

// 恢复测试挂 VerifyLivePanel → useEventSource → new EventSource；jsdom 无原生 EventSource，
// mock useEventSource 返回空流（恢复测试只验"进入 live 态不落空态"，不验 SSE 事件本身）。
vi.mock("@/api/useEventSource", () => ({
  useEventSource: () => ({ events: [], status: "open" as const, lastEventId: undefined }),
}));

const failedProf: AuthProfile = {
  id: "prof_1", name: "NG", login_url: "http://t/", login_type: "form",
  credentials: [{
    id: "cred_a", role: "admin", username: "admin",
    verify_status: {
      state: "failed", failure_detail: "Login failed: invalid creds",
      probe_dir: "/p/probe-1", workflow_id: "wf-1",
    },
  }],
};
const unverifiedProf: AuthProfile = {
  id: "prof_1", name: "NG", login_url: "http://t/", login_type: "form",
  credentials: [{ id: "cred_b", role: "user", username: "u1", verify_status: { state: "unverified" } }],
};
const runningProf: AuthProfile = {
  id: "prof_1", name: "NG", login_url: "http://t/", login_type: "form",
  credentials: [{
    id: "cred_a", role: "admin", username: "admin",
    verify_status: {
      state: "running",
      workflow_id: "authval-ws1-probe-running1",
      probe_dir: "/p/probe-running1",
    },
  }],
};

let currentProf: AuthProfile;
let logCalls = 0;
const server = setupServer(
  http.get("/api/workspaces/:ws/auth-profiles/:pid", () => HttpResponse.json(currentProf)),
  http.get("/api/workspaces/:ws/auth-profiles/:pid/credentials/:cid/verify-log", () => {
    logCalls++;
    return HttpResponse.json({ events: [] });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => { i18n.changeLanguage("zh"); logCalls = 0; });
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderPage(cid = "cred_a") {
  return render(
    <MemoryRouter initialEntries={[`/p/ws1/auth-profiles/prof_1/credentials/${cid}`]}>
      <Routes>
        <Route path="/p/:workspace/auth-profiles/:pid/credentials/:cid" element={<VerifyProcessPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("VerifyProcessPage", () => {
  it("加载档案信息: 名称 + role·user + 状态徽章 + failure_detail + 测试登录按钮", async () => {
    currentProf = failedProf;
    renderPage();
    await waitFor(() => expect(screen.getByText("NG")).toBeInTheDocument());
    expect(screen.getByText(/admin · admin/)).toBeInTheDocument();
    expect(screen.getByText("验证失败")).toBeInTheDocument();
    expect(screen.getByText(/Login failed: invalid creds/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "测试登录" })).toBeInTheDocument();
  });

  it("有历史 run → 拉取 verify-log 回看（持久化记录）", async () => {
    currentProf = failedProf;
    renderPage();
    await waitFor(() => expect(screen.getByText("NG")).toBeInTheDocument());
    await waitFor(() => expect(logCalls).toBeGreaterThan(0));
  });

  it("无历史 run → 不拉 verify-log + 显示空态提示", async () => {
    currentProf = unverifiedProf;
    renderPage("cred_b");
    await waitFor(() => expect(screen.getByText("NG")).toBeInTheDocument());
    expect(screen.getByText(/尚未测试登录/)).toBeInTheDocument();
    expect(logCalls).toBe(0);
  });

  it("进行中 run(verify_status=running) → 重挂 VerifyLivePanel 恢复，不落空态", async () => {
    currentProf = runningProf;
    renderPage();
    await waitFor(() => expect(screen.getByText("NG")).toBeInTheDocument());
    // 恢复 effect 识别 running → setTesting(true) 按钮 disable + setLiveRun 挂 VerifyLivePanel（重连 SSE）。
    // 不显示空态"尚未测试登录"（核心 bug：测试进行中离开再回来不该落空态）。
    await waitFor(() => expect(screen.getByRole("button", { name: /正在登录/ })).toBeDisabled());
    expect(screen.queryByText(/尚未测试登录/)).not.toBeInTheDocument();
    // 状态徽章显示「验证中…」
    expect(screen.getByText("验证中…")).toBeInTheDocument();
  });
});
