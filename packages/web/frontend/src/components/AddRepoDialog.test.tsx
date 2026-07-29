import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { AddRepoDialog } from "./AddRepoDialog";

const mockCreateRepo = vi.fn();
const mockLinkReposInDir = vi.fn();
const mockUseAuth = vi.fn();

vi.mock("@/api/client", () => ({
  createRepo: (...a: any[]) => mockCreateRepo(...a),
  linkReposInDir: (...a: any[]) => mockLinkReposInDir(...a),
  ApiError: class ApiError extends Error {
    status: number;
    body: unknown;
    constructor(status: number, body: unknown) {
      super(`HTTP ${status}`);
      this.status = status;
      this.body = body;
    }
  },
}));

vi.mock("@/auth/AuthContext", () => ({ useAuth: () => mockUseAuth() }));

// FileSystemPicker 有自己的测试；这里 mock 它验证 AddRepoDialog 集成（渲染 + onChange 填路径）
vi.mock("@/components/FileSystemPicker", () => ({
  FileSystemPicker: ({ value, onChange, triggerLabel }: {
    value: string; onChange: (v: string) => void; triggerLabel: string;
  }) => (
    <button data-testid="fs-picker" onClick={() => onChange("/app/repos/frontend")}>
      {triggerLabel}{value ? `:${value}` : ""}
    </button>
  ),
}));

function props(overrides: Record<string, unknown> = {}) {
  return { ws: "ws1", open: true, onOpenChange: vi.fn(), onCreated: vi.fn(), ...overrides };
}

describe("AddRepoDialog", () => {
  beforeEach(() => {
    mockCreateRepo.mockReset();
    mockLinkReposInDir.mockReset();
    mockUseAuth.mockReturnValue({ user: { id: 1, username: "tester", role: "admin" } });
  });

  it("批量模式：FileSystemPicker 选路径后提交调 linkReposInDir", async () => {
    mockLinkReposInDir.mockResolvedValue({ imported: [{ name: "frontend", path: "/app/repos/frontend" }], skipped: [] });
    const onCreated = vi.fn();
    const onOpenChange = vi.fn();
    render(<AddRepoDialog {...props({ onCreated, onOpenChange })} />);
    fireEvent.click(await screen.findByTestId("mode-linkdir"));
    // FileSystemPicker 选路径 -> 填入 linkdir-path
    fireEvent.click(screen.getByTestId("fs-picker"));
    expect((screen.getByTestId("linkdir-path") as HTMLInputElement).value).toBe("/app/repos/frontend");
    fireEvent.click(screen.getByTestId("submit"));
    await waitFor(() =>
      expect(mockLinkReposInDir).toHaveBeenCalledWith("ws1", { path: "/app/repos/frontend" }));
    expect(onCreated).toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(mockCreateRepo).not.toHaveBeenCalled();
  });

  it("批量模式：未选路径时提交按钮禁用，选后启用", async () => {
    render(<AddRepoDialog {...props()} />);
    fireEvent.click(await screen.findByTestId("mode-linkdir"));
    expect((screen.getByTestId("submit") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId("fs-picker"));
    expect((screen.getByTestId("submit") as HTMLButtonElement).disabled).toBe(false);
  });

  it("非 admin：不显示批量关联模式（link-dir 为 admin-only）", async () => {
    mockUseAuth.mockReturnValue({ user: { id: 2, username: "member", role: "user" } });
    render(<AddRepoDialog {...props()} />);
    await screen.findByTestId("submit");
    expect(screen.queryByTestId("mode-linkdir")).toBeNull();
    expect(screen.queryByTestId("fs-picker")).toBeNull();
    expect(screen.queryByTestId("mode-clone")).toBeNull();  // 单模式无需切换器
  });

  it("克隆模式：admin 默认克隆，提交调 createRepo", async () => {
    mockCreateRepo.mockResolvedValue({ name: "foo" });
    const onCreated = vi.fn();
    render(<AddRepoDialog {...props({ onCreated })} />);
    fireEvent.change(
      await screen.findByPlaceholderText("https://gitlab.example/foo.git"),
      { target: { value: "https://x/foo.git" } });
    fireEvent.click(screen.getByTestId("submit"));
    await waitFor(() => expect(mockCreateRepo).toHaveBeenCalled());
    expect(onCreated).toHaveBeenCalledWith("foo");
  });
});
