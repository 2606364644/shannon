import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { UserWorkspacesPanel } from "./UserWorkspacesPanel";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const USER = { id: 2, username: "alice", role: "user" as const, must_change_password: false, created_at: "" };

function mockFetchSeq(responses: { status: number; body: any }[]) {
  let i = 0;
  vi.spyOn(window, "fetch").mockImplementation(async () => {
    const r = responses[Math.min(i++, responses.length - 1)];
    return new Response(JSON.stringify(r.body), { status: r.status });
  });
}

describe("UserWorkspacesPanel", () => {
  beforeEach(() => {
    // GET /users/2/workspaces -> 已加入 ws-a(member); GET /workspaces -> [ws-a, ws-b]
    mockFetchSeq([
      { status: 200, body: { workspaces: [{ workspace: "ws-a", role: "member" }] } },
      { status: 200, body: [{ name: "ws-a" }, { name: "ws-b" }] },
    ]);
  });

  it("加载已加入归属 + 全部 ws 清单", async () => {
    render(<UserWorkspacesPanel user={USER} />);
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    expect(screen.getByText("ws-b")).toBeInTheDocument();  // 未加入的也在勾选清单
  });

  it("点加入 ws-b -> POST members", async () => {
    const fm = vi.spyOn(window, "fetch");
    render(<UserWorkspacesPanel user={USER} />);
    await waitFor(() => expect(screen.getByText("ws-b")).toBeInTheDocument());
    // ws-b 行的"加入"按钮
    fireEvent.click(screen.getByTestId("add-ws-b"));
    await waitFor(() => expect(fm.mock.calls.some((c) => (c[1]?.method) === "POST")).toBe(true));
  });
});
