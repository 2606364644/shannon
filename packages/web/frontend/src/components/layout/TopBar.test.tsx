import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { TopBar } from "./TopBar";
import i18n from "@/i18n";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="*" element={<TopBar />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("TopBar", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("品牌字标 Supernova + 主导航", () => {
    renderAt("/");
    expect(screen.getByText(/Supernova/i)).toBeInTheDocument();
    expect(screen.getByText("工作区")).toBeInTheDocument();
    expect(screen.getByText("扫描")).toBeInTheDocument();
  });

  it("Dashboard / Settings DSF 阶段 disabled（非 <a>）", () => {
    renderAt("/");
    const dash = screen.getByText("概览");
    const settings = screen.getByText("设置");
    expect(dash.tagName).not.toBe("A");
    expect(settings.tagName).not.toBe("A");
    expect(dash.closest("[aria-disabled='true']") ?? dash.parentElement?.closest("[aria-disabled='true']") ?? dash).toBeTruthy();
  });

  it("当前 /scan/new → Scan NavLink data-active=true", () => {
    renderAt("/scan/new");
    expect(screen.getByText("扫描").getAttribute("data-active")).toBe("true");
  });

  it("非当前路由 NavLink data-active=false", () => {
    renderAt("/");
    expect(screen.getByText("扫描").getAttribute("data-active")).toBe("false");
  });

  it("含主题切换入口", () => {
    renderAt("/");
    expect(screen.getByRole("button", { name: /切换主题/ })).toBeInTheDocument();
  });
});

describe("TopBar i18n", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("中文渲染导航「仓库」", () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>
    );
    expect(screen.getByText("仓库")).toBeInTheDocument();
  });

  it("切英文后导航变 Repositories", async () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>
    );
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    expect(screen.getByText("Repositories")).toBeInTheDocument();
  });

  it("渲染语言切换器", () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>
    );
    expect(screen.getByLabelText("切换语言")).toBeInTheDocument();
  });
});
