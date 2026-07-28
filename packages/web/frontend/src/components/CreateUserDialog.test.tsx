import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { Toaster } from "@/components/ui/sonner";
import { CreateUserDialog } from "./CreateUserDialog";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

function fill(username: string, password: string) {
  fireEvent.change(screen.getByLabelText("users.username"), { target: { value: username } });
  fireEvent.change(screen.getByLabelText("users.password"), { target: { value: password } });
}

function renderDialog(open: boolean, onOpenChange: () => void, onCreated: () => void) {
  return render(
    <>
      <CreateUserDialog open={open} onOpenChange={onOpenChange} onCreated={onCreated} />
      <Toaster />
    </>,
  );
}

describe("CreateUserDialog", () => {
  beforeEach(() => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
  });

  it("提交成功调 onCreated 并关闭", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockResolvedValue(new Response(JSON.stringify({ user: { id: 3 } }), { status: 200 }));
    const onCreated = vi.fn(), onOpenChange = vi.fn();
    renderDialog(true, onOpenChange, onCreated);
    fill("bob", "bob-pw-12");
    fireEvent.click(screen.getByRole("button", { name: "users.create" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    const body = JSON.parse(fm.mock.calls[0][1]?.body as string);
    expect(body).toEqual({ username: "bob", password: "bob-pw-12", role: "user" });
  });

  it("username 重复(409)透传后端 detail 且不关闭", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "username exists" }), { status: 409 }),
    );
    const onOpenChange = vi.fn();
    renderDialog(true, onOpenChange, vi.fn());
    fill("alice", "alice-pw-12");
    fireEvent.click(screen.getByRole("button", { name: "users.create" }));
    await waitFor(() => expect(screen.getByText("username exists")).toBeInTheDocument());
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("密码不足 8 位时不提交且提示长度要求", async () => {
    const fm = vi.spyOn(window, "fetch");
    renderDialog(true, vi.fn(), vi.fn());
    fill("bob", "123");
    fireEvent.click(screen.getByRole("button", { name: "users.create" }));
    await waitFor(() => expect(screen.getByText("users.passwordMinLength")).toBeInTheDocument());
    expect(fm).not.toHaveBeenCalled();
  });
});
