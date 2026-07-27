import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { RequireAdmin } from "./RequireAdmin";
import { AuthContext, type AuthState } from "./AuthContext";

function wrap(user: AuthState["user"], loading = false) {
  const value: AuthState = {
    user, loading,
    login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn(),
  };
  return render(
    <MemoryRouter>
      <AuthContext.Provider value={value}>
        <RequireAdmin><div>protected</div></RequireAdmin>
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

describe("RequireAdmin", () => {
  it("admin 渲染 children", () => {
    wrap({ id: 1, username: "admin", role: "admin", must_change_password: false });
    expect(screen.getByText("protected")).toBeInTheDocument();
  });

  it("非 admin 跳转(Navigate to /)", () => {
    wrap({ id: 2, username: "alice", role: "user", must_change_password: false });
    expect(screen.queryByText("protected")).toBeNull();
  });
});
