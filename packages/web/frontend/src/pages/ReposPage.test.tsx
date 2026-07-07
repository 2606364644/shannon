import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router-dom";
import { ReposPage } from "./ReposPage";
import { Toaster } from "@/components/ui/sonner";

const server = setupServer(
  http.get("/api/repos", () => HttpResponse.json([
    { name: "foo", state: "ready", source: { kind: "git", url: "https://x/foo.git", branch: "main" } },
    { name: "bar", state: "failed", source: { kind: "git" } },
  ])),
  http.delete("/api/repos/:name", ({ params }) => HttpResponse.json({ deleted: params.name })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderPage() {
  render(<MemoryRouter><ReposPage /><Toaster /></MemoryRouter>);
}

describe("ReposPage", () => {
  it("列出仓库 + 状态", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("foo")).toBeInTheDocument());
    expect(screen.getByText("✗ 失败")).toBeInTheDocument();
  });

  it("删除确认 Dialog", async () => {
    const spy = vi.spyOn(console, "log").mockImplementation(() => {});
    renderPage();
    await waitFor(() => expect(screen.getByText("foo")).toBeInTheDocument());
    fireEvent.click(screen.getAllByText("删除")[0]);
    // DialogTitle(<h2>) 和 DialogDescription(<p>"删除仓库 foo？…") 都含「删除仓库」，
    // 用 exact 精确命中 title（description 含「删除仓库 foo…」不等于「删除仓库」）。
    expect(await screen.findByText("删除仓库", { exact: true })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    await waitFor(() => expect(screen.queryByText("删除仓库", { exact: true })).toBeNull());
    spy.mockRestore();
  });
});
