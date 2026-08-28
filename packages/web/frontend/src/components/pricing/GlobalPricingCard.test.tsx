import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { GlobalPricingCard } from "./GlobalPricingCard";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

const mockUser = { id: 1, username: "u", role: "admin", must_change_password: false };
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: mockUser, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }),
}));

const VIEW = {
  currency: "CNY",
  models: [
    { model: "glm-5.2", prices: { input: 8, output: 28, cache_read: 2, cache_creation: 0 }, source: "global" },
  ],
  has_global_table: true,
  builtin_defaults: { "glm-5.2": { input: 8, output: 28, cache_read: 2, cache_creation: 0 } },
};

function mockFetch(handlers: Record<string, (init?: RequestInit) => unknown>) {
  return vi.spyOn(window, "fetch").mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : (input as Request).url;
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
  mockUser.role = "admin";
  vi.restoreAllMocks();
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("GlobalPricingCard", () => {
  it("admin：编辑器可编辑；改价保存发 PUT /api/pricing", async () => {
    const puts: string[] = [];
    mockFetch({
      "/api/pricing": (init) => {
        if (init?.method === "PUT") puts.push(String(init.body));
        return VIEW;
      },
    });
    render(<GlobalPricingCard />);
    await waitFor(() => expect(screen.getByTestId("pricing-editor-global")).toBeInTheDocument());
    expect(screen.getByTestId("pricing-save")).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("pricing-cell-glm-5.2-input"), { target: { value: "8.5" } });
    fireEvent.click(screen.getByTestId("pricing-save"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toContain('"currency":"CNY"');
    expect(puts[0]).toContain('"glm-5.2"');
    expect(puts[0]).toContain("8.5");
  });

  it("非 admin：只读（无保存 / 无清除），仍展示生效表与来源", async () => {
    mockUser.role = "user";
    mockFetch({ "/api/pricing": () => VIEW });
    render(<GlobalPricingCard />);
    await waitFor(() => expect(screen.getByTestId("pricing-editor-global")).toBeInTheDocument());
    expect(screen.queryByTestId("pricing-save")).not.toBeInTheDocument();
    expect(screen.queryByTestId("pricing-clear")).not.toBeInTheDocument();
    expect(screen.getByTestId("pricing-readonly-glm-5.2-input").textContent).toBe("8");
  });

  it("has_global_table → 清除按钮；confirm 确认后发 DELETE", async () => {
    const dels: string[] = [];
    mockFetch({
      "/api/pricing": (init) => {
        if (init?.method === "DELETE") dels.push("deleted");
        return VIEW;
      },
    });
    render(<GlobalPricingCard />);
    await waitFor(() => expect(screen.getByTestId("pricing-clear")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("pricing-clear"));
    await waitFor(() => expect(dels).toHaveLength(1));
  });

  it("confirm 取消 → 不发 DELETE", async () => {
    const dels: string[] = [];
    mockFetch({
      "/api/pricing": (init) => {
        if (init?.method === "DELETE") dels.push("deleted");
        return VIEW;
      },
    });
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<GlobalPricingCard />);
    await waitFor(() => expect(screen.getByTestId("pricing-clear")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("pricing-clear"));
    await new Promise((r) => setTimeout(r, 50));
    expect(dels).toHaveLength(0);
  });

  it("table_corrupt → 损坏横幅", async () => {
    mockFetch({ "/api/pricing": () => ({ ...VIEW, table_corrupt: true }) });
    render(<GlobalPricingCard />);
    await waitFor(() => expect(screen.getByTestId("pricing-corrupt-banner")).toBeInTheDocument());
  });
});
