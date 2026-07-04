import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { FileSystemPicker } from "./FileSystemPicker";

const ROOT = "/tmp/test-root";
const SUB = `${ROOT}/sub`;

const server = setupServer(
  http.get("/api/fs/browse", (req) => {
    const path = new URL(req.request.url).searchParams.get("path");
    if (path === ROOT) {
      return HttpResponse.json({
        path: ROOT, parent: "/tmp",
        entries: [
          { name: "sub", type: "dir" },
          { name: "a.txt", type: "file", size: 10 },
        ],
      });
    }
    if (path === SUB) {
      return HttpResponse.json({ path: SUB, parent: ROOT, entries: [] });
    }
    if (path === "/nope") {
      return HttpResponse.json({ detail: "path not found" }, { status: 404 });
    }
    return HttpResponse.json({ path: path ?? "/", parent: null, entries: [] });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  localStorage.clear();
  cleanup();
});
afterAll(() => server.close());

function renderPicker(props: { value?: string; onChange?: (v: string) => void } = {}) {
  let value = props.value ?? "";
  const onChange = props.onChange ?? ((v: string) => { value = v; });
  const r = render(
    <FileSystemPicker value={value} onChange={onChange} {...props} />,
  );
  return { ...r, getValue: () => value };
}

describe("FileSystemPicker", () => {
  it("打开 Dialog → 列目录 entries", async () => {
    renderPicker({ value: ROOT });
    fireEvent.click(screen.getByRole("button", { name: /浏览/ }));
    await waitFor(() => expect(screen.getByText("sub")).toBeInTheDocument());
    expect(screen.getByText("a.txt")).toBeInTheDocument();
  });

  it("双击目录 → 进入子目录", async () => {
    renderPicker({ value: ROOT });
    fireEvent.click(screen.getByRole("button", { name: /浏览/ }));
    await waitFor(() => expect(screen.getByText("sub")).toBeInTheDocument());
    fireEvent.doubleClick(screen.getByText("sub"));
    await waitFor(() => expect(screen.getByText(/空目录|empty/i)).toBeInTheDocument());
  });

  it("单击目录选中 + '选择此目录'启用；单击文件不启用", async () => {
    renderPicker({ value: ROOT });
    fireEvent.click(screen.getByRole("button", { name: /浏览/ }));
    await waitFor(() => expect(screen.getByText("sub")).toBeInTheDocument());
    const confirmBtn = screen.getByRole("button", { name: /选择此目录/ });
    expect(confirmBtn).toBeDisabled();
    fireEvent.click(screen.getByText("sub"));
    expect(confirmBtn).not.toBeDisabled();
    // 点文件不启用确认
    fireEvent.click(screen.getByText("a.txt"));
    expect(confirmBtn).toBeDisabled();
  });

  it("确认 → onChange 回填 + 书签写入 localStorage", async () => {
    const onChange = vi.fn();
    renderPicker({ value: ROOT, onChange });
    fireEvent.click(screen.getByRole("button", { name: /浏览/ }));
    await waitFor(() => expect(screen.getByText("sub")).toBeInTheDocument());
    fireEvent.click(screen.getByText("sub"));
    fireEvent.click(screen.getByRole("button", { name: /选择此目录/ }));
    expect(onChange).toHaveBeenCalledWith(SUB);
    const recent = JSON.parse(localStorage.getItem("shannon-fs-recent") ?? "[]");
    expect(recent).toContain(SUB);
  });

  it("404 → inline 错误，不关 Dialog", async () => {
    renderPicker({ value: "/nope" });
    fireEvent.click(screen.getByRole("button", { name: /浏览/ }));
    await waitFor(() => expect(screen.getByText(/not found|不存在/)).toBeInTheDocument());
    // Dialog 仍在
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
