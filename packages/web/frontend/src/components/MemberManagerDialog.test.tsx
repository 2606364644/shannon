import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import { MemberManagerDialog } from "./MemberManagerDialog";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
// useAuth 必须返回稳定引用：组件 useEffect 依赖 [ws, user]，若每次渲染返新对象会触发
// getMembers -> setMembers -> 重渲染 -> 新 user 引用 -> effect 再跑 的无限循环（真 AuthProvider 用 useState 保 user 稳定）。
const { authUser } = vi.hoisted(() => ({
  authUser: { id: 2, username: "alice", role: "user", must_change_password: false },
}));
vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ user: authUser }) }));

function mockSeq(responses: { status: number; body: any }[]) {
  let i = 0;
  vi.spyOn(window, "fetch").mockImplementation(async () => {
    const r = responses[Math.min(i++, responses.length - 1)];
    return new Response(JSON.stringify(r.body), { status: r.status });
  });
}

describe("MemberManagerDialog", () => {
  beforeEach(() => {
    // GET members -> alice 是 manager（能见按钮）
    mockSeq([
      { status: 200, body: { members: [{ user_id: 2, username: "alice", role: "manager" }] } },
    ]);
  });

  it("手输 username 加入(不调 listUsers)", async () => {
    const fm = vi.spyOn(window, "fetch");
    render(<MemoryRouter><MemberManagerDialog ws="ws-a" /><Toaster /></MemoryRouter>);
    // 首渲染 members=[] -> canManage=false 不显按钮；等 getMembers 回来后按钮才出现
    fireEvent.click(await screen.findByTestId("member-manager"));
    const input = await screen.findByPlaceholderText("members.input.placeholder");
    fireEvent.change(input, { target: { value: "bob" } });
    // 后续 POST addMember + GET getMembers 都返 members 形态（addMember 忽略 body，getMembers 取 .members）
    fm.mockResolvedValue(new Response(JSON.stringify({ members: [{ user_id: 2, username: "alice", role: "manager" }] }), { status: 200 }));
    fireEvent.click(screen.getByRole("button", { name: "members.add" }));
    await waitFor(() => {
      const addCall = fm.mock.calls.find((c) => (c[0] as string)?.includes("/members") && (c[1] as any)?.method === "POST");
      expect(addCall).toBeTruthy();
    });
    // 确认未调 GET /users（listUsers 已删）：无 method 的 /api/users 调用应不存在
    const usersGet = fm.mock.calls.find((c) => (c[0] as string)?.includes("/api/users") && !((c[1] as any)?.method));
    expect(usersGet).toBeUndefined();
  });

  it("加入不存在用户(404)提示错误", async () => {
    render(<MemoryRouter><MemberManagerDialog ws="ws-a" /><Toaster /></MemoryRouter>);
    fireEvent.click(await screen.findByTestId("member-manager"));
    const input = await screen.findByPlaceholderText("members.input.placeholder");
    fireEvent.change(input, { target: { value: "nobody" } });
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 404 }));
    fireEvent.click(screen.getByRole("button", { name: "members.add" }));
    await waitFor(() => expect(screen.getByText("members.input.notFound")).toBeInTheDocument());
  });
});
