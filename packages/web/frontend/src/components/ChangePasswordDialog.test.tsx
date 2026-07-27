import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ChangePasswordDialog } from "./ChangePasswordDialog";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

function renderDialog(open = true, onOpenChange = vi.fn(), onChanged = vi.fn()) {
  return render(
    <ChangePasswordDialog open={open} onOpenChange={onOpenChange} onChanged={onChanged} />,
  );
}

function fillAndSubmit(oldPw: string, newPw: string, confirmPw: string) {
  fireEvent.change(screen.getByLabelText("auth.changePassword.old"), { target: { value: oldPw } });
  fireEvent.change(screen.getByLabelText("auth.changePassword.new"), { target: { value: newPw } });
  fireEvent.change(screen.getByLabelText("auth.changePassword.confirm"), { target: { value: confirmPw } });
  fireEvent.click(screen.getByRole("button", { name: "auth.changePassword.submit" }));
}

describe("ChangePasswordDialog", () => {
  beforeEach(() => {
    // 占位 mock；各用例内 mockResolvedValue 覆盖具体响应。返回 resolved Response 以满足 fetch 类型。
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
  });

  it("open=true 渲染三个输入字段", () => {
    renderDialog(true);
    expect(screen.getByLabelText("auth.changePassword.old")).toBeInTheDocument();
    expect(screen.getByLabelText("auth.changePassword.new")).toBeInTheDocument();
    expect(screen.getByLabelText("auth.changePassword.confirm")).toBeInTheDocument();
  });

  it("open=false 不渲染", () => {
    renderDialog(false);
    expect(screen.queryByLabelText("auth.changePassword.old")).toBeNull();
  });

  it("新密码与确认不一致时提示错误且不提交", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    renderDialog(true);
    fillAndSubmit("oldpw-123", "newpw-456", "newpw-999");
    await waitFor(() => expect(screen.getByText("auth.changePassword.mismatch")).toBeInTheDocument());
    expect(fm).not.toHaveBeenCalled();
  });

  it("提交成功调 onChanged 并关闭", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    const onChanged = vi.fn();
    const onOpenChange = vi.fn();
    renderDialog(true, onOpenChange, onChanged);
    fillAndSubmit("oldpw-123", "newpw-456", "newpw-456");
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(onOpenChange).toHaveBeenCalledWith(false);
    const callBody = JSON.parse(fm.mock.calls[0][1]?.body as string);
    expect(callBody).toEqual({ old_password: "oldpw-123", new_password: "newpw-456" });
  });

  it("旧密码错(401)提示错误且不关闭", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockResolvedValue(new Response(JSON.stringify({ detail: "invalid credentials" }), { status: 401 }));
    const onOpenChange = vi.fn();
    renderDialog(true, onOpenChange, vi.fn());
    fillAndSubmit("oldpw-123", "newpw-456", "newpw-456");
    await waitFor(() => expect(screen.getByText("auth.changePassword.wrongOld")).toBeInTheDocument());
    expect(onOpenChange).not.toHaveBeenCalled();
  });
});
