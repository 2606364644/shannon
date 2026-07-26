import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { AuthProvider } from "@/auth/AuthContext";
import { RequireAuth } from "./RequireAuth";

function Protected() {
  return <div>protected content</div>;
}

function wrap(initial = "/secret") {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/login" element={<div>login page</div>} />
          <Route path={initial} element={<RequireAuth><Protected /></RequireAuth>} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>
  );
}

describe("RequireAuth", () => {
  it("未登录跳 /login", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 401 }));
    wrap();
    await waitFor(() => expect(screen.getByText("login page")).toBeTruthy());
    expect(screen.queryByText("protected content")).toBeNull();
  });

  it("loading 时显示 Loading 屏", () => {
    vi.spyOn(window, "fetch").mockImplementation(() => new Promise(() => {})); // 永久 pending
    wrap();
    expect(screen.getByText(/Loading/i)).toBeTruthy();
  });
});
