import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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
});
