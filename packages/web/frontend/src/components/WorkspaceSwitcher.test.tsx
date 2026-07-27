import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";

const { mockRefresh, mockDeleteWorkspace, mockNav, mockUseAuth } = vi.hoisted(() => ({
  mockRefresh: vi.fn().mockResolvedValue(undefined),
  mockDeleteWorkspace: vi.fn().mockResolvedValue({ deleted: "ws-a" }),
  mockNav: vi.fn(),
  mockUseAuth: vi.fn(),
}));

vi.mock("@/api/useWorkspaces", () => ({
  useWorkspaces: () => ({
    data: [
      { name: "ws-a", status: "running", scan_type: "whitebox", created_at: 1, scan_count: 2 },
      { name: "ws-b", status: "completed", scan_type: "blackbox", created_at: 2, scan_count: 1 },
    ],
    loading: false, lastUpdated: new Date(), error: null, refresh: mockRefresh,
  }),
}));

vi.mock("@/auth/AuthContext", () => ({ useAuth: () => mockUseAuth() }));

vi.mock("@/api/client", () => ({ deleteWorkspace: (...a: any[]) => mockDeleteWorkspace(...a) }));

vi.mock("react-router-dom", async (orig) => {
  const actual = await orig() as any;
  return { ...actual, useNavigate: () => mockNav };
});

vi.mock("@/components/CreateWorkspaceDialog", () => ({
  CreateWorkspaceDialog: () => <div data-testid="create-ws-dialog" />,
}));

const userAdmin = { id: 1, username: "admin", role: "admin", must_change_password: false, pinned_workspace: "ws-a" };
const userUser = { id: 2, username: "alice", role: "user", must_change_password: false, pinned_workspace: null };

// jsdom navigator.language 默认 en，LanguageDetector 会把 i18n 切到 en；
// 断言依赖中文渲染（getByRole button name=/切换/i），逐测试钉回 zh。
beforeEach(() => {
  vi.clearAllMocks();
  mockUseAuth.mockReturnValue({ user: userAdmin });
  return i18n.changeLanguage("zh");
});

function renderIt(currentWs = "ws-a") {
  return render(<MemoryRouter><WorkspaceSwitcher currentWorkspace={currentWs} /></MemoryRouter>);
}

describe("WorkspaceSwitcher", () => {
  it("opens drawer on trigger click and lists workspaces", async () => {
    renderIt();
    fireEvent.click(screen.getByRole("button", { name: /切换/i }));
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    expect(screen.getByText("ws-b")).toBeInTheDocument();
  });

  it("highlights current workspace", async () => {
    renderIt("ws-a");
    fireEvent.click(screen.getByRole("button", { name: /切换/i }));
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    expect(screen.getByText("ws-a").closest("[data-current]")).toHaveAttribute("data-current", "true");
  });

  it("search filters list", async () => {
    renderIt();
    fireEvent.click(screen.getByRole("button", { name: /切换/i }));
    fireEvent.change(screen.getByPlaceholderText(/搜索/), { target: { value: "ws-b" } });
    expect(screen.queryByText("ws-a")).not.toBeInTheDocument();
    expect(screen.getByText("ws-b")).toBeInTheDocument();
  });

  it("shows create-workspace entry for admin", async () => {
    renderIt();
    fireEvent.click(screen.getByRole("button", { name: /切换/i }));
    await waitFor(() => expect(screen.getByTestId("create-ws-dialog")).toBeInTheDocument());
  });
});

describe("WorkspaceSwitcher admin 删除入口（spec 2026-07-27 下线 WorkspaceListPage：删除并入切换器）", () => {
  beforeEach(() => {
    mockUseAuth.mockReturnValue({ user: userAdmin });
  });

  it("admin: 每行显示 trash 删除按钮", async () => {
    renderIt();
    fireEvent.click(screen.getByRole("button", { name: /切换/i }));
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    expect(screen.queryByTestId("switcher-delete-ws-a")).toBeInTheDocument();
    expect(screen.queryByTestId("switcher-delete-ws-b")).toBeInTheDocument();
  });

  it("admin: 点 trash→确认 Dialog→调 deleteWorkspace(name) + refresh（删非 current 不跳转）", async () => {
    mockDeleteWorkspace.mockResolvedValue({ deleted: "ws-b" });
    renderIt("ws-a"); // current=ws-a，删 ws-b
    fireEvent.click(screen.getByRole("button", { name: /切换/i }));
    await waitFor(() => expect(screen.getByText("ws-b")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("switcher-delete-ws-b"));
    const confirm = await screen.findByRole("button", { name: /^确认$/ });
    fireEvent.click(confirm);
    await waitFor(() => expect(mockDeleteWorkspace).toHaveBeenCalledWith("ws-b"));
    expect(mockRefresh).toHaveBeenCalled();
    expect(mockNav).not.toHaveBeenCalled();
  });

  it("admin: 删的是 currentWorkspace → nav('/')", async () => {
    renderIt("ws-a"); // current=ws-a，删自己
    fireEvent.click(screen.getByRole("button", { name: /切换/i }));
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("switcher-delete-ws-a"));
    const confirm = await screen.findByRole("button", { name: /^确认$/ });
    fireEvent.click(confirm);
    await waitFor(() => expect(mockDeleteWorkspace).toHaveBeenCalledWith("ws-a"));
    expect(mockNav).toHaveBeenCalledWith("/");
  });

  it("普通用户:无 trash 按钮（gate）", async () => {
    mockUseAuth.mockReturnValue({ user: userUser });
    renderIt();
    fireEvent.click(screen.getByRole("button", { name: /切换/i }));
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    expect(screen.queryByTestId("switcher-delete-ws-a")).not.toBeInTheDocument();
  });
});
