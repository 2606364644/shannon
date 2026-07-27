import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ConfirmDeleteUserDialog } from "./ConfirmDeleteUserDialog";
import type { UserRow } from "@/api/users";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string, o?: any) => k.replace("{{name}}", o?.name ?? "") }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe("ConfirmDeleteUserDialog", () => {
  beforeEach(() => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
  });

  it("需点确认按钮才删(防误删)", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    const onDeleted = vi.fn(), onOpenChange = vi.fn();
    const user: UserRow = { id: 2, username: "alice", role: "user", must_change_password: false, created_at: "" };
    render(<ConfirmDeleteUserDialog user={user} open onOpenChange={onOpenChange} onDeleted={onDeleted} />);
    fireEvent.click(screen.getByRole("button", { name: "users.deleteConfirmBtn" }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
    expect(fm.mock.calls[0][1]?.method).toBe("DELETE");
  });
});
