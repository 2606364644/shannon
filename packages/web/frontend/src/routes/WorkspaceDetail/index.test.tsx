import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import WorkspaceDetail from "./index";

function renderAt(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/p/:workspace" element={<WorkspaceDetail />}>
          <Route path="overview" element={<div>ov-content</div>} />
          <Route path="report" element={<div>rp-content</div>} />
          <Route path="deliverables" element={<div>dl-content</div>} />
          <Route path="logs" element={<div>lg-content</div>} />
          <Route path="live" element={<div>lv-content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("WorkspaceDetail shell", () => {
  it("渲染 tablist 与 5 个 tab role", () => {
    renderAt("/p/ws/overview");
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.getAllByRole("tab")).toHaveLength(5);
  });

  it("当前 tab 由路由段决定（aria-selected）", () => {
    renderAt("/p/ws/logs");
    expect(screen.getByRole("tab", { name: "日志" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "概览" })).toHaveAttribute("aria-selected", "false");
  });

  it("mousedown 一个 tab 触发导航", () => {
    renderAt("/p/ws/overview");
    fireEvent.mouseDown(screen.getByRole("tab", { name: "实时" }));
    expect(screen.getByText("lv-content")).toBeInTheDocument();
  });
});
