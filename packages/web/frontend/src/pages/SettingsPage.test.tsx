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
  version: "supernova-web 0.1.0",
  brand_name: "Supernova",
};

const server = setupServer(
  http.get("/api/system-status", () => HttpResponse.json(okBody)),
  http.get("/api/branding", () => HttpResponse.json({ brand_name: null })),
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
  document.documentElement.classList.remove("dark", "light");
});
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

describe("SettingsPage", () => {
  it("渲染三分区 eyebrow（个人化/系统/关于）", async () => {
    renderWithTheme(<SettingsPage />);
    expect(await screen.findByText("个人化")).toBeInTheDocument();
    expect(screen.getByText("系统")).toBeInTheDocument();
    expect(screen.getByText("关于")).toBeInTheDocument();
  });

  it("状态面板渲染各字段(ai_provider/temporal/version)", async () => {
    renderWithTheme(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("claude")).toBeInTheDocument());
    expect(screen.getByText("agent-browser")).toBeInTheDocument();
    expect(screen.getByText("localhost:7233")).toBeInTheDocument();
    expect(screen.getByText("supernova-web 0.1.0")).toBeInTheDocument();
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

  it("主题 segmented：点浅色 → <html>.light + localStorage=light", async () => {
    renderWithTheme(<SettingsPage />);
    await screen.findByText("个人化");
    fireEvent.click(screen.getByRole("button", { name: "浅色" }));
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(localStorage.getItem("supernova-theme")).toBe("light");
  });

  it("主题 segmented：点跟随系统 → localStorage=system", async () => {
    renderWithTheme(<SettingsPage />);
    await screen.findByText("个人化");
    fireEvent.click(screen.getByRole("button", { name: "跟随系统" }));
    expect(localStorage.getItem("supernova-theme")).toBe("system");
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

  it("中文渲染页标题与三分区 eyebrow", async () => {
    renderWithTheme(<SettingsPage />);
    expect(await screen.findByText("设置")).toBeInTheDocument();
    expect(screen.getByText("个人化")).toBeInTheDocument();
    expect(screen.getByText("系统")).toBeInTheDocument();
    expect(screen.getByText("关于")).toBeInTheDocument();
  });

  it("切英文后 eyebrow 变 Personalization/System/About", async () => {
    renderWithTheme(<SettingsPage />);
    await screen.findByText("设置");
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    expect(await screen.findByText("Personalization")).toBeInTheDocument();
    expect(screen.getByText("System")).toBeInTheDocument();
    expect(screen.getByText("About")).toBeInTheDocument();
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
