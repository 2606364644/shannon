import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup, act } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
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
// jsdom navigator.language 默认 en，LanguageDetector 会把 i18n 切到 en；现有断言依赖中文渲染，逐测试钉回 zh。
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderPage() {
  return render(<MemoryRouter><ReposPage /><Toaster /></MemoryRouter>);
}

describe("ReposPage", () => {
  it("列出仓库 + 状态", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("foo")).toBeInTheDocument());
    expect(screen.getByText("✗ 失败")).toBeInTheDocument();
  });

  it("有来源 URL 的行渲染复制按钮，无 URL 行不渲染", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("foo")).toBeInTheDocument());
    // foo 有 url → CopyButton（aria-label 含仓库名）
    expect(screen.getByRole("button", { name: "复制 foo 的来源 URL" })).toBeInTheDocument();
    // bar 无 url → 不渲染复制按钮
    expect(screen.queryByRole("button", { name: /复制 bar/ })).toBeNull();
  });

  it("大小列内容单行不换行（whitespace-nowrap，防「132.1 MB」在窄列里断行）", async () => {
    server.use(
      http.get("/api/repos", () => HttpResponse.json([
        { name: "foo", state: "ready", source: { kind: "git", url: "https://x/foo.git", branch: "main" }, size_bytes: 132_074_317 },
      ])),
    );
    renderPage();
    // 概览条「总大小」也显示同一值 → 取表格里的 td（概览条值在 div.tabular-nums，无 td 祖先）
    const cells = await screen.findAllByText("132.1 MB");
    const cell = cells.find((c) => c.closest("td"));
    expect(cell?.closest("td")?.className).toMatch(/whitespace-nowrap/);
  });

  it("State 列内容单行不换行（英文「⚠ Incomplete」等长状态值不断行）", async () => {
    server.use(
      http.get("/api/repos", () => HttpResponse.json([
        { name: "foo", state: "stale", source: { kind: "git" } },
      ])),
    );
    renderPage();
    await waitFor(() => expect(screen.getByText("foo")).toBeInTheDocument());
    await act(async () => { await i18n.changeLanguage("en"); });
    // stale 英文 = "⚠ Incomplete"（11 字符，最长 state 值）；锚定 state 单元格 nowrap 防断行
    const cell = await screen.findByText(/Incomplete/);
    expect(cell.closest("td")?.className).toMatch(/whitespace-nowrap/);
  });

  it("操作列表头与按钮组居中对齐（text-center：表头恒在按钮组中心正上方，无论 1 或 2 个按钮）", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("foo")).toBeInTheDocument());
    // 表头 th 居中
    expect(screen.getByText("操作").closest("th")?.className).toMatch(/text-center/);
    // 删除按钮所在 td 居中（按钮组 inline-flex 受 text-align:center 居中）
    const delBtn = screen.getAllByRole("button", { name: /删除/ })[0];
    expect(delBtn.closest("td")?.className).toMatch(/text-center/);
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
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it("cloning / stale 行显对应指示（CloneProgress / 未完成）", async () => {
    server.use(
      http.get("/api/repos", () => HttpResponse.json([
        { name: "wip", state: "cloning", source: { kind: "git" } },
        { name: "old", state: "stale", source: { kind: "git" } },
      ])),
      // CloneProgress 走 SSE；空流不影响「clone 中」渲染
      http.get("/api/repos/wip/events", () => new HttpResponse("", { headers: { "Content-Type": "text/event-stream" } })),
    );
    renderPage();
    await waitFor(() => expect(screen.getByText("wip")).toBeInTheDocument());
    expect(screen.getByText(/clone 中/)).toBeInTheDocument();
    expect(screen.getByText(/未完成/)).toBeInTheDocument();
  });

  it("按分组折叠渲染（group/repo 跨组同名不冲突）", async () => {
    server.use(
      http.get("/api/repos", () => HttpResponse.json([
        { name: "frontend/honor", group: "frontend", state: "ready", source: { kind: "git", url: "https://x/hon-fe.git" } },
        { name: "backend/honor", group: "backend", state: "ready", source: { kind: "git", url: "https://x/hon-be.git" } },
      ])),
    );
    renderPage();
    // 两个分组 section 都渲染（section 标题 button 含分组名 + 计数）
    expect(await screen.findByRole("button", { name: /frontend \(1\)/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /backend \(1\)/ })).toBeInTheDocument();
    // 同名 honor 跨组共存：两个链接都在
    const honorLinks = screen.getAllByRole("link", { name: /honor/ });
    expect(honorLinks).toHaveLength(2);
  });

  it("精修：渲染副标题", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("foo")).toBeInTheDocument());
    expect(screen.getByText(/已纳管/)).toBeInTheDocument();
  });

  it("精修：概览条聚合 仓库数/就绪/克隆中", async () => {
    const { container } = renderPage();
    await waitFor(() => expect(screen.getByText("foo")).toBeInTheDocument());
    const statValue = (label: string) =>
      Array.from(container.querySelectorAll(".uppercase"))
        .find((n) => n.textContent === label)?.nextElementSibling?.textContent ?? "";
    // foo ready + bar failed → 仓库 2 / 就绪 1 / 克隆中 0
    expect(statValue("仓库")).toBe("2");
    expect(statValue("就绪")).toBe("1");
    expect(statValue("克隆中")).toBe("0");
  });

  it("精修：分组 header 带边框（section 化）", async () => {
    server.use(
      http.get("/api/repos", () => HttpResponse.json([
        { name: "a/x", group: "g1", state: "ready", source: { kind: "git" } },
      ])),
    );
    renderPage();
    await waitFor(() => expect(screen.getByText("a/x")).toBeInTheDocument());
    const grpBtn = screen.getByRole("button", { name: /g1/ });
    expect(grpBtn.className).toMatch(/border/);
  });
});

describe("ReposPage i18n", () => {
  afterEach(() => i18n.changeLanguage("zh"));

  it("中文显示标题与空状态", async () => {
    server.use(http.get("/api/repos", () => HttpResponse.json([])));
    renderPage();
    // 标题用 heading role 定位（概览条 label「仓库」与 h1 同文本）
    expect(await screen.findByRole("heading", { name: "仓库" })).toBeInTheDocument();
    expect(screen.getByText(/暂无仓库/)).toBeInTheDocument();
  });

  it("切英文后标题变 Repositories", async () => {
    server.use(http.get("/api/repos", () => HttpResponse.json([])));
    renderPage();
    await screen.findByRole("heading", { name: "仓库" });
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    expect(await screen.findByRole("heading", { name: "Repositories" })).toBeInTheDocument();
  });
});
