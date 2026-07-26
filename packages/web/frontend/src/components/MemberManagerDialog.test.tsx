import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "@/auth/AuthContext";
import { MemberManagerDialog } from "./MemberManagerDialog";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

function wrap() {
  return render(
    <AuthProvider>
      <MemoryRouter><MemberManagerDialog ws="ws1" /></MemoryRouter>
    </AuthProvider>
  );
}

describe("MemberManagerDialog", () => {
  it("manager 可见管理入口", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockResolvedValueOnce(new Response(JSON.stringify({ user: { id: 2, username: "alice", role: "user" } }), { status: 200 })); // /me
    fm.mockResolvedValue(new Response(JSON.stringify({ members: [{ user_id: 2, username: "alice", role: "manager" }] }), { status: 200 })); // members
    wrap();
    await waitFor(() => expect(screen.getByText("members.manage")).toBeTruthy());
  });

  it("非成员/非 manager 隐藏入口", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockResolvedValueOnce(new Response(JSON.stringify({ user: { id: 3, username: "bob", role: "user" } }), { status: 200 }));
    fm.mockResolvedValue(new Response(JSON.stringify({ members: [{ user_id: 2, username: "alice", role: "manager" }] }), { status: 200 }));
    const { container } = wrap();
    await waitFor(() => expect(container.querySelector("[data-testid=member-manager]") === null).toBe(true));
  });
});
