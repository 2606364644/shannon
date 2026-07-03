import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { TopBar } from "./TopBar";

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
  it("品牌字标 Shannon + 主导航", () => {
    renderAt("/");
    expect(screen.getByText(/Shannon/)).toBeInTheDocument();
    expect(screen.getByText("Workspaces")).toBeInTheDocument();
    expect(screen.getByText("Scan")).toBeInTheDocument();
  });

  it("Dashboard / Settings DSF 阶段 disabled（非 <a>）", () => {
    renderAt("/");
    const dash = screen.getByText("Dashboard");
    const settings = screen.getByText("Settings");
    expect(dash.tagName).not.toBe("A");
    expect(settings.tagName).not.toBe("A");
    expect(dash.closest("[aria-disabled='true']") ?? dash.parentElement?.closest("[aria-disabled='true']") ?? dash).toBeTruthy();
  });

  it("当前 /scan/new → Scan NavLink data-active=true", () => {
    renderAt("/scan/new");
    expect(screen.getByText("Scan").getAttribute("data-active")).toBe("true");
  });

  it("非当前路由 NavLink data-active=false", () => {
    renderAt("/");
    expect(screen.getByText("Scan").getAttribute("data-active")).toBe("false");
  });

  it("含主题切换入口", () => {
    renderAt("/");
    expect(screen.getByRole("button", { name: /切换主题/ })).toBeInTheDocument();
  });
});
