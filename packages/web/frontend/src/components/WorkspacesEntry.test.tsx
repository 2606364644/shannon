import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useParams } from "react-router-dom";
import i18n from "@/i18n";

// vi.mock 工厂被 hoist 到所有 import 之前；用 vi.hoisted 暴露可变 mock 函数，
// 每个测试用 mockReturnValue 切换返回值驱动三段跳转不同分支。
// （brief 原 vi.doMock 写法对静态 import 无效--见 task-11-report.md）
const { mockUseAuth, mockUseWorkspaces } = vi.hoisted(() => ({
  mockUseAuth: vi.fn(),
  mockUseWorkspaces: vi.fn(),
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock("@/api/useWorkspaces", () => ({
  useWorkspaces: () => mockUseWorkspaces(),
}));

import { WorkspacesEntry } from "./WorkspacesEntry";

function WsDetail() {
  const { workspace } = useParams();
  return <div data-testid="ws-detail" data-ws={workspace} />;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/" element={<div data-testid="home" />} />
        <Route path="/p/:workspace" element={<WsDetail />} />
        <Route path="*" element={<WorkspacesEntry />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("WorkspacesEntry", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("redirects to pinned workspace when set", async () => {
    mockUseAuth.mockReturnValue({ user: { pinned_workspace: "ws-pinned" } });
    mockUseWorkspaces.mockReturnValue({ data: [], loading: false });
    const { container } = renderAt("/entry");
    await waitFor(() =>
      expect(container.querySelector("[data-testid='ws-detail']")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("ws-detail")).toHaveAttribute("data-ws", "ws-pinned");
  });

  it("redirects to most recent workspace when no pinned but has membership", async () => {
    mockUseAuth.mockReturnValue({ user: { pinned_workspace: null } });
    mockUseWorkspaces.mockReturnValue({
      data: [
        { name: "ws-old", status: "completed", created_at: 1, latest_created_at: 1, scan_type: "whitebox" },
        { name: "ws-new", status: "completed", created_at: 2, latest_created_at: 2, scan_type: "whitebox" },
      ],
      loading: false,
    });
    const { container } = renderAt("/entry");
    await waitFor(() =>
      expect(container.querySelector("[data-testid='ws-detail']")).toBeInTheDocument(),
    );
    // 最近活跃 = latest_created_at 倒序首项 = ws-new（而非 ws-old）
    expect(screen.getByTestId("ws-detail")).toHaveAttribute("data-ws", "ws-new");
  });

  it("redirects to / (Dashboard) when no membership", async () => {
    mockUseAuth.mockReturnValue({ user: { pinned_workspace: null } });
    mockUseWorkspaces.mockReturnValue({ data: [], loading: false });
    const { container } = renderAt("/entry");
    await waitFor(() =>
      expect(container.querySelector("[data-testid='home']")).toBeInTheDocument(),
    );
  });
});
