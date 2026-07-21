import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "./AppShell";

describe("AppShell", () => {
  it("渲染 TopBar + Outlet 内容", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<div>page-content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    );
    expect(screen.getByText(/Supernova/i)).toBeInTheDocument();
    expect(screen.getByText("page-content")).toBeInTheDocument();
  });
});
