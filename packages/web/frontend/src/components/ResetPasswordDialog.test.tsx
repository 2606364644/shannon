import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { toast } from "sonner";
import { ResetPasswordDialog } from "./ResetPasswordDialog";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe("ResetPasswordDialog", () => {
  beforeEach(() => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
    vi.mocked(toast.error).mockClear();
    vi.mocked(toast.success).mockClear();
  });

  it("提交成功 POST reset-password", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    const onOpenChange = vi.fn();
    render(<ResetPasswordDialog userId={2} open onOpenChange={onOpenChange} />);
    fireEvent.change(screen.getByLabelText("users.newPassword"), { target: { value: "new-pw-12" } });
    fireEvent.click(screen.getByRole("button", { name: "users.resetPassword" }));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    const body = JSON.parse(fm.mock.calls[0][1]?.body as string);
    expect(body).toEqual({ new_password: "new-pw-12" });
  });

  it("密码不足 8 位时不提交且提示长度要求", async () => {
    const fm = vi.spyOn(window, "fetch");
    render(<ResetPasswordDialog userId={2} open onOpenChange={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("users.newPassword"), { target: { value: "123" } });
    fireEvent.click(screen.getByRole("button", { name: "users.resetPassword" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("users.passwordMinLength"));
    expect(fm).not.toHaveBeenCalled();
  });
});
