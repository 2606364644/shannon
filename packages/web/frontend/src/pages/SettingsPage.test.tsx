import type { ReactElement } from "react";
import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup, act } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import { SettingsPage } from "./SettingsPage";
import { ThemeProvider } from "@/lib/theme-context";
import { BrandProvider } from "@/brand/BrandContext";

// useAuth 可控：mock 前缀变量供 hoisted vi.mock 工厂引用（vitest 支持 mock* 前缀）
const mockUser = {
  id: 1,
  username: "admin",
  role: "admin",
  must_change_password: false,
};

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: mockUser,
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));

const okBody = {
  ai_provider: "claude",
  browser_engine: "agent-browser",
  temporal: { enabled: true, host: "localhost:7233", last_status: "connected", last_error: null },
  git: { binary_available: true, credentials_configured: true },
  version: "Supernova 0.1.0",
  brand_name: "Supernova",
};

const server = setupServer(
  http.get("/api/system-status", () => HttpResponse.json(okBody)),
  http.get("/api/branding", () => HttpResponse.json({ brand_name: null })),
  // SSO section（spec 2026-08-26）：默认 handler——admin 渲染配置卡/白名单面板的初始 fetch
  http.get("/api/auth/sso/admin/config", () => HttpResponse.json({
    enabled: false, auth_domain: "", public_base_url: "",
    passport_base: "https://passport.futuoa.com", session_ttl_hours: 24,
    updated_at: "2026-08-26T01:00:00+00:00", updated_by: "seed",
  })),
  http.get("/api/auth/sso/config", () => HttpResponse.json({ enabled: false })),
  http.get("/api/auth/sso/whitelist", () => HttpResponse.json({ whitelist: [], enabled: true })),
);

function renderWithTheme(ui: ReactElement) {
  return render(
    <ThemeProvider>
      <BrandProvider>{ui}</BrandProvider>
    </ThemeProvider>,
  );
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
// jsdom navigator.language 默认 en，LanguageDetector 会把 i18n 切到 en；现有断言依赖中文渲染，逐测试钉回 zh。
beforeEach(async () => {
  // i18n.changeLanguage 触发 react-i18next 重渲染，须在 act 内 await，否则 act 警告
  await act(async () => {
    await i18n.changeLanguage("zh");
  });
  mockUser.must_change_password = false;
  localStorage.clear();
  document.documentElement.classList.remove(
    "dark", "light", "theme-mac", "theme-midnight", "theme-graphite",
    "theme-sentry", "theme-arc", "theme-mission", "theme-github", "theme-notion", "theme-kami",
  );
});
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

describe("SettingsPage", () => {
  it("渲染各分区 eyebrow（品牌/个人化/系统）", async () => {
    renderWithTheme(<SettingsPage />);
    expect(await screen.findByText("个人化")).toBeInTheDocument();
    expect(screen.getByText("系统")).toBeInTheDocument();
    expect(screen.queryByText("关于")).not.toBeInTheDocument();
  });

  it("状态面板渲染各字段(ai_provider/temporal/version)", async () => {
    renderWithTheme(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("claude")).toBeInTheDocument());
    expect(screen.getByText("agent-browser")).toBeInTheDocument();
    expect(screen.getByText("localhost:7233")).toBeInTheDocument();
    expect(screen.getByText("Supernova 0.1.0")).toBeInTheDocument();
    // git 拆成两个独立信号(二进制 / GitLab 凭据)
    expect(screen.getByText("已装")).toBeInTheDocument();
    expect(screen.getByText("已配置")).toBeInTheDocument();
  });

  it("GitLab 凭据未配置 → 显示未配置提示(本地路径模式无需)", async () => {
    server.use(http.get("/api/system-status", () => HttpResponse.json({
      ...okBody,
      git: { binary_available: true, credentials_configured: false },
    })));
    renderWithTheme(<SettingsPage />);
    await waitFor(() => expect(screen.getByText(/未配置/)).toBeInTheDocument());
  });

  it("主题选择器：点 Mac → <html>.light + theme-mac + localStorage=mac", async () => {
    renderWithTheme(<SettingsPage />);
    await screen.findByText("个人化");
    fireEvent.click(screen.getByRole("button", { name: /Mac/ }));
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("theme-mac")).toBe(true);
    expect(localStorage.getItem("supernova-theme")).toBe("mac");
  });

  it("主题选择器：渲染 Claude 双主题新标签（charcoal/warm-paper 改名后）", async () => {
    renderWithTheme(<SettingsPage />);
    await screen.findByText("个人化");
    expect(screen.getByRole("button", { name: /Claude 深色/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Claude 浅色/ })).toBeInTheDocument();
    // 旧名不再出现（改名闭环）
    expect(screen.queryByRole("button", { name: /^炭黑$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^暖纸$/ })).not.toBeInTheDocument();
  });

  it("主题选择器：点午夜 → <html>.dark + theme-midnight + localStorage=midnight", async () => {
    renderWithTheme(<SettingsPage />);
    await screen.findByText("个人化");
    fireEvent.click(screen.getByRole("button", { name: /午夜/ }));
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.classList.contains("theme-midnight")).toBe(true);
    expect(localStorage.getItem("supernova-theme")).toBe("midnight");
  });

  it("主题选择器：点跟随系统 → localStorage=system 且 palette class 清空", async () => {
    localStorage.setItem("supernova-theme", "midnight");
    renderWithTheme(<SettingsPage />);
    await screen.findByText("个人化");
    expect(document.documentElement.classList.contains("theme-midnight")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /跟随系统/ }));
    expect(localStorage.getItem("supernova-theme")).toBe("system");
    expect(document.documentElement.className).not.toContain("theme-");
  });

  it("主题选择器：新六主题全部渲染 + 点 kami → light + theme-kami", async () => {
    renderWithTheme(<SettingsPage />);
    await screen.findByText("个人化");
    for (const label of ["Sentry 紫黑", "Arc 玻璃", "指挥中心", "GitHub", "Notion 暖灰", "kami 纸质"]) {
      expect(screen.getByRole("button", { name: new RegExp(label) })).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole("button", { name: /kami 纸质/ }));
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("theme-kami")).toBe(true);
    expect(localStorage.getItem("supernova-theme")).toBe("kami");
  });

  it("must_change_password=true → 显示改密提醒 badge", async () => {
    mockUser.must_change_password = true;
    renderWithTheme(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("修改默认密码")).toBeInTheDocument());
  });

  it("status fetch 失败 → 局部 ErrorState(role=alert)，主题卡仍在", async () => {
    server.use(http.get("/api/system-status", () => HttpResponse.json({}, { status: 500 })));
    renderWithTheme(<SettingsPage />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText("主题")).toBeInTheDocument();
  });
});

describe("SettingsPage i18n", () => {
  afterEach(async () => {
    await act(async () => {
      await i18n.changeLanguage("zh");
    });
  });

  it("中文渲染页标题与各分区 eyebrow", async () => {
    renderWithTheme(<SettingsPage />);
    expect(await screen.findByText("设置")).toBeInTheDocument();
    expect(screen.getByText("个人化")).toBeInTheDocument();
    expect(screen.getByText("系统")).toBeInTheDocument();
    expect(screen.queryByText("关于")).not.toBeInTheDocument();
  });

  it("切英文后 eyebrow 变 Personalization/System", async () => {
    renderWithTheme(<SettingsPage />);
    await screen.findByText("设置");
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    expect(await screen.findByText("Personalization")).toBeInTheDocument();
    expect(screen.getByText("System")).toBeInTheDocument();
    expect(screen.queryByText("About")).not.toBeInTheDocument();
  });
});

describe("SettingsPage 品牌名编辑", () => {
  beforeEach(async () => {
    mockUser.role = "admin";
    await act(async () => {
      await i18n.changeLanguage("zh");
    });
  });

  it("渲染品牌区 eyebrow + 预览字标", async () => {
    renderWithTheme(<SettingsPage />);
    expect(await screen.findByText("品牌")).toBeInTheDocument();
    // 预览框内显示当前品牌名 Supernova
    expect(screen.getAllByText("Supernova").length).toBeGreaterThan(0);
  });

  it("admin: 输入改名 → 预览即时反映 + Save 调 PUT /api/branding", async () => {
    const puts: unknown[] = [];
    server.use(
      http.put("/api/branding", async ({ request }) => {
        const body = (await request.json()) as { brand_name: string };
        puts.push(body);
        const eff = body.brand_name ?? "Supernova";
        return HttpResponse.json({ brand_name: body.brand_name, effective: eff });
      }),
    );
    renderWithTheme(<SettingsPage />);
    await screen.findByText("品牌");
    const input = await screen.findByLabelText("名称");
    fireEvent.change(input, { target: { value: "Acme Sec" } });
    // 预览即时反映输入
    expect(screen.getByText("Acme Sec")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("brand-save"));
    await waitFor(() => expect(puts).toEqual([{ brand_name: "Acme Sec" }]));
  });

  it("admin: 空名/同名时 Save 禁用", async () => {
    renderWithTheme(<SettingsPage />);
    await screen.findByText("品牌");
    // 初始 draft=当前名 → dirty=false → 禁用
    expect(screen.getByTestId("brand-save")).toBeDisabled();
  });

  it("admin: 超长(>32)显示计数为红 + Save 禁用", async () => {
    renderWithTheme(<SettingsPage />);
    await screen.findByText("品牌");
    const input = await screen.findByLabelText("名称");
    fireEvent.change(input, { target: { value: "x".repeat(33) } });
    expect(screen.getByTestId("brand-save")).toBeDisabled();
  });

  it("admin: 无覆盖时 reset 禁用;改名保存后 reset 可用", async () => {
    let brandOverride: string | null = null;
    server.use(
      http.get("/api/branding", () => HttpResponse.json({ brand_name: brandOverride })),
      http.put("/api/branding", async ({ request }) => {
        const body = (await request.json()) as { brand_name: string | null };
        brandOverride = body.brand_name;
        const eff = body.brand_name ?? "Supernova";
        return HttpResponse.json({ brand_name: body.brand_name, effective: eff });
      }),
    );
    renderWithTheme(<SettingsPage />);
    await screen.findByText("品牌");
    // reset 按钮是第二个 ghost 按钮(title=恢复默认);初始无覆盖 → 禁用
    const resetBtn = screen.getByTitle("恢复默认");
    await waitFor(() => expect(resetBtn).toBeDisabled());
    // 改名保存 → 有覆盖 → reset 启用
    fireEvent.change(await screen.findByLabelText("名称"), { target: { value: "Acme" } });
    fireEvent.click(screen.getByTestId("brand-save"));
    await waitFor(() => expect(resetBtn).not.toBeDisabled());
  });

  it("非 admin: 只读(无保存按钮 + 锁标) + 显示当前名", async () => {
    mockUser.role = "user";
    renderWithTheme(<SettingsPage />);
    await screen.findByText("品牌");
    expect(screen.getByText("仅管理员可改名")).toBeInTheDocument();
    expect(screen.queryByTestId("brand-save")).not.toBeInTheDocument();
  });
});

describe("SettingsPage SSO 配置（spec 2026-08-26 运行时化）", () => {
  beforeEach(async () => {
    mockUser.role = "admin";
    await act(async () => {
      await i18n.changeLanguage("zh");
    });
  });

  const cfgBody = {
    enabled: false, auth_domain: "", public_base_url: "",
    passport_base: "https://passport.futuoa.com", session_ttl_hours: 24,
    updated_at: "2026-08-26T01:00:00+00:00", updated_by: "seed",
  };

  it("admin: SSO section 渲染——eyebrow + 配置卡回显 + 白名单面板迁入", async () => {
    renderWithTheme(<SettingsPage />);
    expect(await screen.findByText("SSO / OA 登录")).toBeInTheDocument();
    // 配置卡异步加载完成(msw 默认 handler 回默认配置)
    const card = await screen.findByTestId("sso-config-card");
    expect(card).toBeInTheDocument();
    // GET admin config 回显(passport 默认基址 / 更新者)
    expect((screen.getByLabelText(/OA 基址/) as HTMLInputElement).value).toBe("https://passport.futuoa.com");
    expect(screen.getByText(/seed/)).toBeInTheDocument();
    // 白名单面板（自 UsersPage 迁入）挂载于本 section
    expect(screen.getByTestId("sso-whitelist-panel")).toBeInTheDocument();
  });

  it("admin: 开开关+填域名 → 保存调 PUT 全量 body,成功后回显更新者", async () => {
    const puts: unknown[] = [];
    server.use(
      http.put("/api/auth/sso/admin/config", async ({ request }) => {
        puts.push(await request.json());
        return HttpResponse.json({ ...cfgBody, enabled: true, auth_domain: "codescan.test.local", updated_by: "admin" });
      }),
    );
    renderWithTheme(<SettingsPage />);
    await screen.findByTestId("sso-config-card");
    fireEvent.click(screen.getByTestId("sso-config-toggle"));
    fireEvent.change(screen.getByLabelText(/本站域名/), { target: { value: "codescan.test.local" } });
    fireEvent.click(screen.getByTestId("sso-config-save"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toMatchObject({
      enabled: true, auth_domain: "codescan.test.local",
      passport_base: "https://passport.futuoa.com", session_ttl_hours: 24,
    });
    expect(await screen.findByText(/admin/)).toBeInTheDocument();
  });

  it("admin: 保存 400 → 内联错误提示(testid),不崩", async () => {
    server.use(
      http.put("/api/auth/sso/admin/config", () =>
        HttpResponse.json({ detail: "auth_domain is required when enabled" }, { status: 400 })),
    );
    renderWithTheme(<SettingsPage />);
    await screen.findByTestId("sso-config-card");
    fireEvent.click(screen.getByTestId("sso-config-toggle"));
    fireEvent.click(screen.getByTestId("sso-config-save"));
    await waitFor(() => expect(screen.getByTestId("sso-config-error")).toBeInTheDocument());
  });

  it("非 admin: SSO section 整体不渲染", async () => {
    mockUser.role = "user";
    renderWithTheme(<SettingsPage />);
    await screen.findByText("个人化");
    expect(screen.queryByText("SSO / OA 登录")).not.toBeInTheDocument();
    expect(screen.queryByTestId("sso-config-card")).not.toBeInTheDocument();
    expect(screen.queryByTestId("sso-whitelist-panel")).not.toBeInTheDocument();
  });
});
