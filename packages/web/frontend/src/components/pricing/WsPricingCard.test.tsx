import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import WsPricingCard from "./WsPricingCard";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

const mockUser = { id: 7, username: "mgr", role: "user", must_change_password: false };
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: mockUser, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }),
}));

vi.mock("react-router-dom", () => ({ useParams: () => ({ workspace: "ws1" }) }));

// members API：user 7 = manager（canEdit）；role 改 member 测只读
const membersBody = () => ({ members: [{ user_id: 7, username: "mgr", role: "manager" }] });

const INHERIT = {
  currency: "CNY",
  models: [
    { model: "glm-5.2", prices: { input: 8, output: 28, cache_read: 2, cache_creation: 0 }, source: "global" },
    { model: "deepseek-v4-pro", prices: { input: 3, output: 6, cache_read: 0.025, cache_creation: 0 }, source: "profile_env" },
  ],
  override_exists: false,
  builtin_defaults: { "glm-5.2": { input: 8, output: 28, cache_read: 2, cache_creation: 0 } },
};

const OVERRIDDEN = {
  ...INHERIT,
  models: [
    { model: "glm-5.2", prices: { input: 7, output: 26, cache_read: 1, cache_creation: 0 }, source: "workspace" },
  ],
  override_exists: true,
};

function mockFetch(handlers: Record<string, (init?: RequestInit) => unknown>) {
  return vi.spyOn(window, "fetch").mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : (input as Request).url;
    if (url.includes("/api/workspaces/ws1/members")) {
      return Promise.resolve(new Response(JSON.stringify(membersBody()), { status: 200 }));
    }
    for (const [frag, fn] of Object.entries(handlers)) {
      if (url.includes(frag)) {
        const body = fn(init);
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }
    }
    return Promise.resolve(new Response("{}", { status: 404 }));
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("WsPricingCard", () => {
  it("继承态：只读生效表 + 来源徽章；manager 可见「覆盖」入口，点击进入编辑", async () => {
    mockFetch({ "/api/workspaces/ws1/pricing": () => INHERIT });
    render(<WsPricingCard />);
    await waitFor(() => expect(screen.getByTestId("ws-pricing-inherit-note")).toBeInTheDocument());
    // 只读：来源徽章可见（profile_env 态如实呈现 env 手写层）
    expect(screen.getByTestId("pricing-source-deepseek-v4-pro").textContent).toBe("pricing.source.profile_env");
    expect(screen.queryByTestId("pricing-save")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("ws-pricing-override-btn"));
    expect(screen.getByTestId("pricing-save")).toBeInTheDocument();
  });

  it("覆盖态：workspace scope 编辑器 + 已覆盖徽标 + 清除覆盖（confirm 后 DELETE）", async () => {
    const dels: string[] = [];
    mockFetch({
      "/api/workspaces/ws1/pricing": (init) => {
        if (init?.method === "DELETE") dels.push("deleted");
        return OVERRIDDEN;
      },
    });
    render(<WsPricingCard />);
    await waitFor(() => expect(screen.getByTestId("ws-pricing-overridden")).toBeInTheDocument());
    expect(screen.getByTestId("pricing-source-glm-5.2").textContent).toBe("pricing.source.workspace");
    fireEvent.click(screen.getByTestId("pricing-clear"));
    await waitFor(() => expect(dels).toHaveLength(1));
  });

  it("manager 改价保存发 PUT /workspaces/{ws}/pricing", async () => {
    const puts: string[] = [];
    mockFetch({
      "/api/workspaces/ws1/pricing": (init) => {
        if (init?.method === "PUT") puts.push(String(init.body));
        return OVERRIDDEN;
      },
    });
    render(<WsPricingCard />);
    await waitFor(() => expect(screen.getByTestId("pricing-save")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("pricing-cell-glm-5.2-input"), { target: { value: "6.5" } });
    fireEvent.click(screen.getByTestId("pricing-save"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toContain("6.5");
    expect(puts[0]).toContain('"currency":"CNY"');
  });

  it("行级币种：切换进 PUT payload（模型级 currency 字段）", async () => {
    const puts: string[] = [];
    mockFetch({
      "/api/workspaces/ws1/pricing": (init) => {
        if (init?.method === "PUT") puts.push(String(init.body));
        return OVERRIDDEN;
      },
    });
    render(<WsPricingCard />);
    await waitFor(() => expect(screen.getByTestId("pricing-save")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("pricing-row-currency-glm-5.2-USD"));
    fireEvent.click(screen.getByTestId("pricing-save"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toContain('"currency":"USD"');
  });

  it("member（非 manager）：继承态无覆盖按钮；覆盖态只读", async () => {
    vi.spyOn(window, "fetch").mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url.includes("/api/workspaces/ws1/members")) {
        return Promise.resolve(new Response(
          JSON.stringify({ members: [{ user_id: 7, username: "mgr", role: "member" }] }), { status: 200 }));
      }
      if (url.includes("/api/workspaces/ws1/pricing")) {
        return Promise.resolve(new Response(JSON.stringify(INHERIT), { status: 200 }));
      }
      return Promise.resolve(new Response("{}", { status: 404 }));
    });
    render(<WsPricingCard />);
    await waitFor(() => expect(screen.getByTestId("ws-pricing-inherit-note")).toBeInTheDocument());
    expect(screen.queryByTestId("ws-pricing-override-btn")).not.toBeInTheDocument();
    expect(screen.queryByTestId("pricing-save")).not.toBeInTheDocument();
  });
});
