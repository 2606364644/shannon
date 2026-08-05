// 块4: CredentialRow 过程可见——验证后展开显示 verify-log agent 登录每步。
// Harness mirrors AuthProfilesPage.test.tsx（msw + i18n.changeLanguage("zh")）。
import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import { CredentialRow } from "./CredentialRow";
import type { AuthProfile, AuthProfileCredential } from "@/api/types";

const prof: AuthProfile = {
  id: "prof_1", name: "NG", login_url: "http://t/", login_type: "form", credentials: [],
};
const failedCred: AuthProfileCredential = {
  id: "cred_a", role: "admin", username: "admin",
  verify_status: {
    state: "failed", failure_detail: "Login failed without diagnostic",
    probe_dir: "/workspaces/ws1/auth-probes/probe-1", workflow_id: "authval-ws1-probe-1",
  },
};
const successCred: AuthProfileCredential = {
  id: "cred_b", role: "user", username: "u1",
  verify_status: {
    state: "success",
    probe_dir: "/workspaces/ws1/auth-probes/probe-2", workflow_id: "authval-ws1-probe-2",
  },
};

const server = setupServer(
  http.get("/api/workspaces/:ws/auth-profiles/:pid/credentials/:cid/verify-log", () =>
    HttpResponse.json({ events: [
      { agent: "validate-auth", msg: "navigate to /login" },
      { agent: "validate-auth", msg: "submit credentials" },
    ] })),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

// textContent 聚合：祖先元素也含事件文本,故用 getAllByText（容忍多匹配）+ length 断言。
describe("CredentialRow 过程可见（块4）", () => {
  it("失败凭据默认展开显示 verify-log 过程事件", async () => {
    i18n.changeLanguage("zh");
    render(<CredentialRow ws="ws1" profile={prof} credential={failedCred} onChanged={() => {}} />);
    await waitFor(() => expect(screen.getAllByText(/navigate to \/login/).length).toBeGreaterThan(0));
    expect(screen.getAllByText(/submit credentials/).length).toBeGreaterThan(0);
  });

  it("成功凭据点「查看过程」展开显示事件", async () => {
    i18n.changeLanguage("zh");
    render(<CredentialRow ws="ws1" profile={prof} credential={successCred} onChanged={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /查看过程/ }));
    await waitFor(() => expect(screen.getAllByText(/navigate to \/login/).length).toBeGreaterThan(0));
  });
});
