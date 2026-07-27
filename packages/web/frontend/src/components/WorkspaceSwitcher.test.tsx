import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";

vi.mock("@/api/useWorkspaces", () => ({
  useWorkspaces: () => ({
    data: [
      { name: "ws-a", status: "running", scan_type: "whitebox", created_at: 1, scan_count: 2 },
      { name: "ws-b", status: "completed", scan_type: "blackbox", created_at: 2, scan_count: 1 },
    ],
    loading: false, lastUpdated: new Date(), error: null, refresh: vi.fn(),
  }),
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 1, username: "admin", role: "admin", must_change_password: false, pinned_workspace: "ws-a" } }),
}));

vi.mock("@/components/CreateWorkspaceDialog", () => ({
  CreateWorkspaceDialog: () => <div data-testid="create-ws-dialog" />,
}));

// jsdom navigator.language 默认 en，LanguageDetector 会把 i18n 切到 en；
// 断言依赖中文渲染（getByRole button name=/切换/i），逐测试钉回 zh。
beforeEach(() => i18n.changeLanguage("zh"));

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
