import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "@/auth/AuthContext";
import { Toaster } from "@/components/ui/sonner";
import { CreateWorkspaceDialog } from "./CreateWorkspaceDialog";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

function wrap(onCreated: () => void = () => {}) {
  return render(
    <AuthProvider>
      <MemoryRouter>
        <CreateWorkspaceDialog onCreated={onCreated} />
        <Toaster />
      </MemoryRouter>
    </AuthProvider>,
  );
}

describe("CreateWorkspaceDialog", () => {
  it("admin 可见新建按钮", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ user: { id: 1, username: "admin", role: "admin" } }), { status: 200 }),
    );
    wrap();
    await waitFor(() => expect(screen.getByText("workspace.create.button")).toBeTruthy());
  });

  it("非 admin 隐藏按钮（self-gate）", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ user: { id: 2, username: "alice", role: "user" } }), { status: 200 }),
    );
    const { container } = wrap();
    // 等401或user加载完，不应出现 button
    await waitFor(() => {
      expect(container.querySelector("button")).toBeNull();
    });
  });

  it("创建失败 → toast 错误 + 弹窗保留（spec：错误不卡死也不静默）", async () => {
    const fm = vi.spyOn(window, "fetch");
    // 1) /auth/me → admin; 2) POST /workspaces → 409 重名冲突
    fm.mockResolvedValueOnce(
      new Response(JSON.stringify({ user: { id: 1, username: "admin", role: "admin" } }), { status: 200 }),
    );
    fm.mockResolvedValue(
      new Response(JSON.stringify({ detail: "workspace exists" }), { status: 409 }),
    );

    wrap();
    await waitFor(() => expect(screen.getByText("workspace.create.button")).toBeTruthy());

    // 打开 dialog → 输入名 → 提交
    fireEvent.click(screen.getByText("workspace.create.button"));
    const input = await screen.findByLabelText("workspace.create.name");
    fireEvent.change(input, { target: { value: "new-ws" } });
    fireEvent.click(screen.getByText("workspace.create.submit"));

    // toast 错误出现 + dialog 保留（标题仍可见，submit 按钮可重试）
    await waitFor(() => expect(screen.getByText(/workspace\.create\.failed/)).toBeInTheDocument());
    expect(screen.getByText("workspace.create.title")).toBeInTheDocument();
    expect(screen.getByText("workspace.create.submit")).not.toBeDisabled();
  });

  it("创建成功 → onCreated 回调 + 关闭 dialog", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockResolvedValueOnce(
      new Response(JSON.stringify({ user: { id: 1, username: "admin", role: "admin" } }), { status: 200 }),
    );
    fm.mockResolvedValueOnce(
      new Response(JSON.stringify({ name: "new-ws" }), { status: 200 }),
    );

    const onCreated = vi.fn();
    wrap(onCreated);
    await waitFor(() => expect(screen.getByText("workspace.create.button")).toBeTruthy());

    fireEvent.click(screen.getByText("workspace.create.button"));
    const input = await screen.findByLabelText("workspace.create.name");
    fireEvent.change(input, { target: { value: "new-ws" } });
    fireEvent.click(screen.getByText("workspace.create.submit"));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("new-ws"));
    // dialog 关闭：标题不再可见
    await waitFor(() => expect(screen.queryByText("workspace.create.title")).not.toBeInTheDocument());
  });
});
