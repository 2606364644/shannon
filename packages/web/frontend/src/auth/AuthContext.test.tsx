import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { AuthProvider, useAuth } from "./AuthContext";
import { setUnauthorizedHandler } from "@/api/client";

function ShowUser() {
  const { user, loading } = useAuth();
  return <div>{loading ? "loading" : user ? `user:${user.username}` : "anon"}</div>;
}

beforeEach(() => vi.restoreAllMocks());

describe("AuthContext", () => {
  it("mounts anonymous when /me returns 401", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 401 }));
    render(<AuthProvider><ShowUser /></AuthProvider>);
    await waitFor(() => expect(screen.getByText("anon")).toBeTruthy());
  });

  it("mounts logged-in when /me returns user", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ user: { id: 1, username: "alice", role: "user" } }), { status: 200 })
    );
    render(<AuthProvider><ShowUser /></AuthProvider>);
    await waitFor(() => expect(screen.getByText("user:alice")).toBeTruthy());
  });

  it("login 凭证错误不触发过期跳转（由表单 catch 提示）", async () => {
    // 根因：login 401（凭证错/用户不存在）曾因 apiPost 未 silent 而触发
    // onUnauthorized → window.location.assign('/login?expired=1') 整页跳转，
    // 掩盖表单错误提示 → 用户反复输错 → 「一直跳转 /login?expired=1」循环。
    // login 调用应 silent：凭证错归 LoginPage 处理，不等于 session 过期。
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    const fm = vi.spyOn(window, "fetch").mockImplementation((url) => {
      const s = String(url);
      if (s.includes("/auth/csrf"))
        return Promise.resolve(new Response(JSON.stringify({ csrf_token: "t" }), { status: 200 }));
      if (s.includes("/auth/login"))
        return Promise.resolve(new Response(JSON.stringify({ detail: "invalid credentials" }), { status: 401 }));
      return Promise.resolve(new Response("{}", { status: 401 })); // /auth/me 等
    });
    function Probe() {
      const { login } = useAuth();
      return <button onClick={() => login("bad", "pw").catch(() => {})}>go</button>;
    }
    render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => screen.getByRole("button", { name: "go" }));
    fireEvent.click(screen.getByRole("button", { name: "go" }));
    await waitFor(() =>
      expect(fm).toHaveBeenCalledWith("/api/auth/login", expect.objectContaining({ method: "POST" })),
    );
    expect(handler).not.toHaveBeenCalled();
  });

  // ---- SSO 登出（spec 2026-08-25 §8）----
  // jsdom 的 location.assign 属性不可配置，vi.spyOn 会抛 "Cannot redefine property"，
  // 沿 client.test.ts 既有模式整对象替换 window.location；用完 restore 恢复原对象。
  function mockLocationAssign(): { assign: ReturnType<typeof vi.fn>; restore: () => void } {
    const assign = vi.fn();
    const origLoc = window.location;
    Object.defineProperty(window, "location", {
      value: { pathname: "/login", assign } as unknown as Location,
      writable: true,
      configurable: true,
    });
    return {
      assign,
      restore: () => Object.defineProperty(window, "location", { value: origLoc, writable: true, configurable: true }),
    };
  }

  it("logout 响应带 sso_logout_url 时清态并跳转 OA 登出", async () => {
    vi.spyOn(window, "fetch").mockImplementation((url) => {
      const s = String(url);
      if (s.includes("/auth/logout"))
        return Promise.resolve(new Response(
          JSON.stringify({ ok: true, sso_logout_url: "https://passport.test/site/logout.html?returnUrl=x" }),
          { status: 200 }));
      return Promise.resolve(new Response(
        JSON.stringify({ user: { id: 1, username: "alice", role: "user" } }), { status: 200 }));
    });
    const { assign, restore } = mockLocationAssign();
    function Probe() {
      const { user, logout } = useAuth();
      return (
        <div>
          <div>{user ? `user:${user.username}` : "anon"}</div>
          <button onClick={() => void logout()}>go</button>
        </div>
      );
    }
    render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => expect(screen.getByText("user:alice")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "go" }));
    await waitFor(() => expect(screen.getByText("anon")).toBeTruthy());
    expect(assign).toHaveBeenCalledWith("https://passport.test/site/logout.html?returnUrl=x");
    restore();
  });

  it("logout 无 sso_logout_url 维持原行为（不跳转）", async () => {
    vi.spyOn(window, "fetch").mockImplementation((url) => {
      const s = String(url);
      if (s.includes("/auth/logout"))
        return Promise.resolve(new Response(JSON.stringify({ ok: true, sso_logout_url: null }), { status: 200 }));
      return Promise.resolve(new Response(
        JSON.stringify({ user: { id: 1, username: "alice", role: "user" } }), { status: 200 }));
    });
    const { assign, restore } = mockLocationAssign();
    function Probe() {
      const { user, logout } = useAuth();
      return (
        <div>
          <div>{user ? `user:${user.username}` : "anon"}</div>
          <button onClick={() => void logout()}>go</button>
        </div>
      );
    }
    render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => expect(screen.getByText("user:alice")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "go" }));
    // 等 logout 跑完（本地态已清）再断言：账密会话无 sso_logout_url，不应触发 OA 跳转
    await waitFor(() => expect(screen.getByText("anon")).toBeTruthy());
    expect(assign).not.toHaveBeenCalled();
    restore();
  });
});
