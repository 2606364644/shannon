// 块4: 认证「测试登录」实时过程面板——步骤条 + 实时日志（复用 DashboardPanel + LogStream）。
// Harness mirrors LiveTab.test.tsx（vi.mock useEventSource 喂受控 events）。
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import i18n from "@/i18n";
import { VerifyLivePanel } from "./VerifyLivePanel";
import { verifyEventsUrl } from "@/api/authProfiles";

// mock useEventSource 返回受控 events（module-level mutable）
const eventsState: { events: any[]; status: string } = { events: [], status: "open" };
vi.mock("@/api/useEventSource", () => ({
  useEventSource: () => eventsState,
}));

beforeEach(() => {
  i18n.changeLanguage("zh");
  eventsState.events = [];
  eventsState.status = "open";
});

describe("verifyEventsUrl", () => {
  it("拼出含 workflow_id + probe_dir 的 SSE url（probe_dir 编码）", () => {
    const url = verifyEventsUrl("ws1", "prof_1", "cred_a", "authval-ws1-probe-1", "/p/probe");
    expect(url).toBe(
      "/api/workspaces/ws1/auth-profiles/prof_1/credentials/cred_a/verify-events"
      + "?workflow_id=authval-ws1-probe-1&probe_dir=%2Fp%2Fprobe",
    );
  });
});

describe("VerifyLivePanel", () => {
  const steps = ["navigate", "fill_credentials", "submit", "verify_session"];
  const intents = ["导航到登录页", "填写凭据", "提交登录表单", "校验已登录会话"];

  it("PhaseEvent 渲染 4 步步骤条，StepEvent 推进进度", () => {
    eventsState.events = [
      { type: "PhaseEvent", event: "start", phase: "auth-validation",
        steps, step_intents: intents, ts: "2026-01-01T00:00:00Z", category: "PHASE" },
      { type: "StepEvent", event: "complete", name: "navigate", phase: "auth-validation",
        intent: "导航到登录页", ts: "2026-01-01T00:00:01Z", category: "STEP" },
    ];
    render(
      <VerifyLivePanel ws="ws1" pid="prof_1" cid="cred_a"
        workflowId="authval-ws1-probe-1" probeDir="/p/probe"
        onComplete={() => {}} />,
    );
    // 4 个步骤名都渲染
    expect(screen.getByText("navigate")).toBeInTheDocument();
    expect(screen.getByText("fill_credentials")).toBeInTheDocument();
    expect(screen.getByText("verify_session")).toBeInTheDocument();
    // 进度 1/4（navigate 完成）
    expect(screen.getByText(/1\/4/)).toBeInTheDocument();
  });

  it("scan_end 出现后调 onComplete(workflowId, probeDir)", async () => {
    const onComplete = vi.fn();
    eventsState.events = [
      { type: "scan_end", status: "completed", ts: "2026-01-01T00:00:02Z", category: "CONTROL" },
    ];
    render(
      <VerifyLivePanel ws="ws1" pid="prof_1" cid="cred_a"
        workflowId="authval-ws1-probe-1" probeDir="/p/probe"
        onComplete={onComplete} />,
    );
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith("authval-ws1-probe-1", "/p/probe"));
  });
});
