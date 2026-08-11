// Task 11: AuthProfilesPage CRUD(list + 新建/编辑对话框 + 删除)。
// Harness mirrors OverviewTab.test.tsx(msw + MemoryRouter + <Route> + i18n.changeLanguage("zh"));
// brief 的 selector 简化对双 "新建档案" 按钮(工具栏 + 对话框提交)无法消歧, 改用 within(dialog)。
// GET handler 改为有状态(POST 后追加), 反映真实后端语义——否则提交后 refresh 仍返初始列表。
import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import { Toaster } from "@/components/ui/sonner";
import { AuthProfilesPage } from "./AuthProfilesPage";
import type { AuthProfile } from "@/api/types";

const initial: AuthProfile[] = [
  {
    id: "prof_1",
    name: "NG",
    login_url: "http://t/",
    login_type: "form",
    credentials: [
      { id: "cred_a", role: "admin", username: "admin", password: "••••",
        verify_status: { state: "unverified" } },
    ],
  },
];
let profiles: AuthProfile[];

const server = setupServer(
  http.get("/api/workspaces/:ws/auth-profiles", () => HttpResponse.json(profiles)),
  http.post("/api/workspaces/:ws/auth-profiles", async ({ request }) => {
    const b = (await request.json()) as {
      name?: string;
      login_url?: string;
      login_type?: string;
      credentials?: Array<{ role?: string; username?: string }>;
    };
    const newProfile: AuthProfile = {
      id: "prof_new",
      name: b.name ?? "",
      login_url: b.login_url ?? "",
      login_type: (b.login_type ?? "form") as "form" | "sso" | "api" | "basic",
      credentials: [{
        id: "cred_new",
        role: b.credentials?.[0]?.role ?? "admin",
        username: b.credentials?.[0]?.username ?? "",
        verify_status: { state: "unverified" },
      }],
    };
    profiles = [...profiles, newProfile];
    return HttpResponse.json(newProfile);
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
// jsdom navigator.language 默认 en, LanguageDetector 会把 i18n 切到 en; 现有断言依赖中文渲染, 钉回 zh。
beforeEach(() => {
  i18n.changeLanguage("zh");
  profiles = [...initial];
});
afterEach(() => { server.resetHandlers(); cleanup(); if (vi.isFakeTimers()) vi.useRealTimers(); });
afterAll(() => server.close());

function renderPage(ws = "ws1") {
  return render(
    <MemoryRouter initialEntries={[`/p/${ws}/auth-profiles`]}>
      <Routes>
        <Route path="/p/:workspace/auth-profiles" element={<AuthProfilesPage />} />
      </Routes>
      <Toaster />
    </MemoryRouter>,
  );
}

describe("AuthProfilesPage", () => {
  it("渲染档案列表", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("NG")).toBeInTheDocument());
  });

  it("新建档案提交后刷新列表", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("NG")).toBeInTheDocument());
    // 此时对话框未开, "新建档案" 仅工具栏按钮一个匹配
    fireEvent.click(screen.getByText("新建档案"));
    fireEvent.change(screen.getByLabelText("档案名"), { target: { value: "App2" } });
    fireEvent.change(screen.getByLabelText("登录地址"), { target: { value: "http://x/" } });
    fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "u" } });
    // 对话框开后: 工具栏按钮 + DialogTitle + 提交按钮三处都是 "新建档案" 文案。
    // scope 到 dialog 内, 用 role=button + name 精确取提交按钮(排除 DialogTitle)。
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "新建档案" }));
    await waitFor(() => expect(screen.getByText("App2")).toBeInTheDocument());
  });

  it("新建多角色档案：添加 2 行角色 → 提交 credentials 含 2 条（role/username 各异）", async () => {
    let posted: Record<string, unknown> | undefined;
    server.use(http.post("/api/workspaces/:ws/auth-profiles", async ({ request }) => {
      posted = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({
        id: "p2", name: "M", login_url: "http://t/", login_type: "form",
        credentials: [
          { id: "c1", role: "admin", username: "a", verify_status: { state: "unverified" } },
          { id: "c2", role: "user", username: "u", verify_status: { state: "unverified" } },
        ],
      });
    }));
    renderPage();
    await waitFor(() => expect(screen.getByText("NG")).toBeInTheDocument());
    fireEvent.click(screen.getByText("新建档案"));
    fireEvent.change(screen.getByLabelText("档案名"), { target: { value: "M" } });
    fireEvent.change(screen.getByLabelText("登录地址"), { target: { value: "http://t/" } });
    // 第一行（角色默认 admin）填用户名
    fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "a" } });
    // 添加第二行 + 填角色 user / 用户名 u
    fireEvent.click(screen.getByRole("button", { name: /添加角色/ }));
    fireEvent.change(screen.getAllByLabelText("角色")[1], { target: { value: "user" } });
    fireEvent.change(screen.getAllByLabelText("用户名")[1], { target: { value: "u" } });
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "新建档案" }));
    await waitFor(() => expect(posted).toBeDefined());
    const creds = (posted as { credentials: { role: string; username: string }[] }).credentials;
    expect(creds.length).toBe(2);
    expect(creds.map((c) => c.role)).toEqual(["admin", "user"]);
    expect(creds.map((c) => c.username)).toEqual(["a", "u"]);
  });

  it("编辑多角色档案：预填全量角色 + 提交 PUT 透传 id 全量（不丢角色）", async () => {
    let putBody: Record<string, unknown> | undefined;
    profiles = [{
      ...initial[0],
      credentials: [
        { id: "cred_a", role: "admin", username: "admin", password: "••••", verify_status: { state: "unverified" } },
        { id: "cred_b", role: "user", username: "u", password: "••••", verify_status: { state: "unverified" } },
      ],
    }];
    server.use(http.put("/api/workspaces/:ws/auth-profiles/:pid", async ({ request }) => {
      putBody = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ ok: true });
    }));
    renderPage();
    await waitFor(() => expect(screen.getByText("NG")).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText("编辑"));
    const dialog = await screen.findByRole("dialog");
    // 预填全量角色（用户名 admin + u）
    const userInputs = within(dialog).getAllByLabelText("用户名");
    expect(userInputs.map((i) => (i as HTMLInputElement).value)).toEqual(["admin", "u"]);
    // 提交 → PUT credentials 含 2 条，id 透传（password 留空 = 保留原值）
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(putBody).toBeDefined());
    const creds = (putBody as { credentials: { id: string; role: string }[] }).credentials;
    expect(creds.length).toBe(2);
    expect(creds.map((c) => c.id)).toEqual(["cred_a", "cred_b"]);
  });

  // 系统档案（configs seed，scope=system）：只读——隐藏编辑/删除按钮 + 显示系统徽章。
  // 后端已有 403 硬守卫；前端隐藏按钮是 UX（避免无意义操作 + 明确只读来源）。
  it("系统档案隐藏编辑/删除按钮并显示系统徽章", async () => {
    profiles = [
      { ...initial[0], id: "prof_ws", name: "ws-prof", scope: "workspace" },
      {
        ...initial[0], id: "prof_sys", name: "sys-prof", scope: "system",
        credentials: [{
          id: "cred_s", role: "primary", username: "u", password: "••••",
          verify_status: { state: "unverified" },
        }],
      },
    ];
    renderPage();
    await waitFor(() => expect(screen.getByText("sys-prof")).toBeInTheDocument());
    // 仅 ws-prof 行渲染编辑/删除按钮（系统行隐藏）
    expect(screen.getAllByLabelText("编辑")).toHaveLength(1);
    expect(screen.getAllByLabelText("删除")).toHaveLength(1);
    // 系统档案显示来源徽章
    expect(screen.getByText("系统")).toBeInTheDocument();
  });

  // fork：系统档案「复制到工作区」→ ws 可编辑副本
  it("系统档案显示「复制到工作区」按钮，ws 档案不显示", async () => {
    profiles = [
      { ...initial[0], id: "prof_ws", name: "ws-prof", scope: "workspace" },
      {
        ...initial[0], id: "prof_sys", name: "sys-prof", scope: "system",
        credentials: [{
          id: "cred_s", role: "primary", username: "u", password: "••••",
          verify_status: { state: "unverified" },
        }],
      },
    ];
    renderPage();
    await waitFor(() => expect(screen.getByText("sys-prof")).toBeInTheDocument());
    // 系统行有「复制到工作区」按钮
    expect(screen.getByRole("button", { name: /复制到工作区/ })).toBeInTheDocument();
  });

  it("点「复制到工作区」fork 成功 → 该行变可编辑（系统徽章消失、编辑出现）", async () => {
    server.use(
      http.post("/api/workspaces/:ws/auth-profiles/:pid/fork", () => {
        // fork 后 GET list 返 ws 副本（scope=workspace，无系统徽章）
        profiles = [{
          ...initial[0], id: "prof_sys", name: "sys-prof", scope: "workspace",
          credentials: [{
            id: "cred_new", role: "primary", username: "u", password: "••••",
            verify_status: { state: "unverified" },
          }],
        }];
        return HttpResponse.json(profiles[0]);
      }),
    );
    profiles = [{
      ...initial[0], id: "prof_sys", name: "sys-prof", scope: "system",
      credentials: [{
        id: "cred_s", role: "primary", username: "u", password: "••••",
        verify_status: { state: "unverified" },
      }],
    }];
    renderPage();
    await waitFor(() => expect(screen.getByText("sys-prof")).toBeInTheDocument());
    expect(screen.getByText("系统")).toBeInTheDocument();   // 初始系统徽章
    fireEvent.click(screen.getByRole("button", { name: /复制到工作区/ }));
    // fork 后刷新：编辑按钮出现，系统徽章消失
    await waitFor(() => expect(screen.getByLabelText("编辑")).toBeInTheDocument());
    expect(screen.queryByText("系统")).not.toBeInTheDocument();
  });

  it("重复 fork 409 → toast「已复制到本工作区」（非失败）", async () => {
    server.use(
      http.post("/api/workspaces/:ws/auth-profiles/:pid/fork", () =>
        HttpResponse.json({ detail: "已复制到本工作区" }, { status: 409 })),
    );
    profiles = [{
      ...initial[0], id: "prof_sys", name: "sys-prof", scope: "system",
      credentials: [{
        id: "cred_s", role: "primary", username: "u", password: "••••",
        verify_status: { state: "unverified" },
      }],
    }];
    renderPage();
    await waitFor(() => expect(screen.getByText("sys-prof")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /复制到工作区/ }));
    await waitFor(() => expect(screen.getByText("已复制到本工作区")).toBeInTheDocument());
  });
});
