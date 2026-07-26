import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { AuthProvider, useAuth } from "./AuthContext";

function ShowUser() {
  const { user, loading } = useAuth();
  return <div>{loading ? "loading" : user ? `user:${user.username}` : "anon"}</div>;
}

beforeEach(() => vi.restoreAllMocks());

describe("AuthContext", () => {
  it("mounts anonymous when /me returns 401", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 401 }));
    render(<AuthProvider><ShowUser /></AuthProvider>);
    await waitFor(() => expect(screen.getByText("anon")).toBeTruthy());
  });

  it("mounts logged-in when /me returns user", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ user: { id: 1, username: "alice", role: "user" } }), { status: 200 })
    );
    render(<AuthProvider><ShowUser /></AuthProvider>);
    await waitFor(() => expect(screen.getByText("user:alice")).toBeTruthy());
  });
});
