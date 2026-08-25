import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { AuthProvider } from "@/auth/AuthContext";
import { UserMenu } from "./UserMenu";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

describe("UserMenu", () => {
  it("登录后渲染首字母", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ user: { id: 1, username: "alice", role: "user" } }), { status: 200 })
    );
    render(<AuthProvider><UserMenu /></AuthProvider>);
    await waitFor(() => expect(screen.getByText("A")).toBeTruthy()); // alice 首字母
  });

  // ---- SSO 头像（spec 2026-08-25 §8）：avatar_url → <img> 直连（referrerPolicy=no-referrer）；无/加载失败 → 首字母 ----

  function mockMe(user: Record<string, unknown> | null) {
    vi.spyOn(window, "fetch").mockResolvedValue(
      new Response(JSON.stringify(user ? { user } : {}), { status: user ? 200 : 401 })
    );
  }

  it("avatar_url 存在时渲染 img（no-referrer，服务端零参与）", async () => {
    mockMe({ id: 1, username: "niu", role: "user", avatar_url: "https://cdn.test/a.png" });
    render(<AuthProvider><UserMenu /></AuthProvider>);
    const img = await screen.findByTestId("user-avatar-img");
    expect(img.tagName).toBe("IMG");
    expect(img.getAttribute("src")).toBe("https://cdn.test/a.png");
    expect(img.getAttribute("referrerpolicy")).toBe("no-referrer");
  });

  it("无 avatar_url 首字母回退", async () => {
    mockMe({ id: 1, username: "niu", role: "user" });
    render(<AuthProvider><UserMenu /></AuthProvider>);
    await waitFor(() => expect(screen.getByText("N")).toBeTruthy()); // niu 首字母
  });

  it("img onError 回退首字母", async () => {
    mockMe({ id: 1, username: "niu", role: "user", avatar_url: "https://cdn.test/a.png" });
    render(<AuthProvider><UserMenu /></AuthProvider>);
    const img = await screen.findByTestId("user-avatar-img");
    fireEvent.error(img);
    await waitFor(() => expect(screen.getByText("N")).toBeTruthy());
    expect(screen.queryByTestId("user-avatar-img")).toBeNull();
  });
});
