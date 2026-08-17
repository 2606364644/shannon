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

describe("VerifyProcessPage 失败提示（VerifyFailureNote）", () => {
  it("engine 失败 → 引擎提示标题 + 401 子码 + 折叠技术详情", async () => {
    currentProf = {
      ...failedProf,
      credentials: [{
        id: "cred_a", role: "admin", username: "admin",
        verify_status: {
          state: "failed", failure_point: "engine",
          failure_detail: "PentestError: Error code: 401 - {'error': {'code': '401', 'message': '令牌已过期或验证不正确'}}",
          probe_dir: "/p/probe-1", workflow_id: "wf-1",
        },
      }],
    };
    renderPage();
    await waitFor(() => expect(screen.getByText("验证引擎调用失败（与目标站账号密码无关）")).toBeInTheDocument());
    expect(screen.getByText(/工作区设置 → LLM 配置/)).toBeInTheDocument();
    expect(screen.getByText("技术详情")).toBeInTheDocument();
  });

  it("旧记录兜底：out_of_band + detail 含 401 签名 → 按 engine 渲染", async () => {
    currentProf = {
      ...failedProf,
      credentials: [{
        id: "cred_a", role: "admin", username: "admin",
        verify_status: {
          state: "failed", failure_point: "out_of_band",
          failure_detail: "PentestError: Error code: 401 - {'error': {'code': '401', 'message': '令牌已过期或验证不正确'}}",
          probe_dir: "/p/probe-1", workflow_id: "wf-1",
        },
      }],
    };
    renderPage();
    await waitFor(() => expect(screen.getByText("验证引擎调用失败（与目标站账号密码无关）")).toBeInTheDocument());
  });

  it("out_of_band 普通失败 → 常规提示（不误报引擎）", async () => {
    currentProf = failedProf;  // detail "Login failed: invalid creds"，无引擎签名
    renderPage();
    await waitFor(() => expect(screen.getByText("登录失败：表单提交之外的问题")).toBeInTheDocument());
    expect(screen.queryByText(/验证引擎调用失败/)).not.toBeInTheDocument();
  });
});
