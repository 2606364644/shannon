// BranchCombobox（spec 2026-08-21 §3）：仓库列表分支列行内下拉——点开 lazy 拉远端分支、
// 关键字筛选、手输兜底、当前分支 ✓ no-op、枚举失败降级手输。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { SWRConfig } from "swr";
import { BranchCombobox } from "./BranchCombobox";

vi.mock("react-i18next", () => {
  const t = (k: string) => k;
  return { useTranslation: () => ({ t }) };
});

let fetchCalls: string[] = [];

function mockBranches(branches: string[] | { status: number; body: unknown }) {
  vi.spyOn(window, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    fetchCalls.push(url);
    if (!(branches as { status?: number }).status) {
      return new Response(JSON.stringify({ branches }), { status: 200 });
    }
    const err = branches as { status: number; body: unknown };
    return new Response(JSON.stringify(err.body), { status: err.status });
  });
}

function renderCombobox(props: { value?: string | null; onSwitch?: (b: string) => void } = {}) {
  const onSwitch = props.onSwitch ?? vi.fn();
  render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <BranchCombobox ws="ws1" repo="app" value={props.value ?? "main"} onSwitch={onSwitch} />
    </SWRConfig>,
  );
  return onSwitch;
}

beforeEach(() => { fetchCalls = []; });
afterEach(() => { vi.restoreAllMocks(); cleanup(); });

describe("BranchCombobox", () => {
  it("触发器显示当前分支", () => {
    mockBranches([]);
    renderCombobox({ value: "main" });
    expect(screen.getByLabelText("repoDetail.switchAria")).toBeTruthy();
    expect(screen.getByText("main")).toBeTruthy();
  });

  it("lazy：挂载不发枚举请求，点开触发器才拉远端分支列表", async () => {
    mockBranches(["dev", "feat/x", "main"]);
    renderCombobox();
    await new Promise((r) => setTimeout(r, 50));
    expect(fetchCalls.filter((u) => u.includes("/branches"))).toHaveLength(0);
    fireEvent.click(screen.getByLabelText("repoDetail.switchAria"));
    await waitFor(() =>
      expect(fetchCalls.some((u) => u.includes("/api/workspaces/ws1/repos/app/branches"))).toBe(true));
    expect(await screen.findByText("dev")).toBeTruthy();
    expect(screen.getByText("feat/x")).toBeTruthy();
  });

  it("输入关键字筛选：只显示匹配项", async () => {
    mockBranches(["dev", "feat/x", "main"]);
    renderCombobox();
    fireEvent.click(screen.getByLabelText("repoDetail.switchAria"));
    const input = await screen.findByPlaceholderText("repoDetail.branchSearch");
    fireEvent.change(input, { target: { value: "feat" } });
    await waitFor(() => {
      expect(screen.getByRole("option", { name: /feat\/x/ })).toBeTruthy();
      expect(screen.queryByRole("option", { name: /^dev$/ })).toBeNull();
      expect(screen.queryByRole("option", { name: /^main$/ })).toBeNull();
    });
  });

  it("无匹配时显示手输兜底项，点击触发 onSwitch(手输分支)", async () => {
    mockBranches(["dev", "main"]);
    const onSwitch = renderCombobox({ onSwitch: vi.fn() });
    fireEvent.click(screen.getByLabelText("repoDetail.switchAria"));
    const input = await screen.findByPlaceholderText("repoDetail.branchSearch");
    fireEvent.change(input, { target: { value: "brand-new" } });
    const fallback = await screen.findByText(/brand-new/);
    fireEvent.click(fallback);
    await waitFor(() => expect(onSwitch).toHaveBeenCalledWith("brand-new"));
  });

  it("当前分支项带选中标记，点击当前分支不触发 onSwitch（no-op）", async () => {
    mockBranches(["dev", "main"]);
    const onSwitch = renderCombobox({ onSwitch: vi.fn() });
    fireEvent.click(screen.getByLabelText("repoDetail.switchAria"));
    const cur = await waitFor(() => {
      const el = screen.getByRole("option", { name: /^main$/ });
      expect(el.getAttribute("aria-current")).toBe("true");
      return el;
    });
    fireEvent.click(cur);
    await new Promise((r) => setTimeout(r, 30));
    expect(onSwitch).not.toHaveBeenCalled();
  });

  it("点击其他分支触发 onSwitch(branch)", async () => {
    mockBranches(["dev", "main"]);
    const onSwitch = renderCombobox({ onSwitch: vi.fn() });
    fireEvent.click(screen.getByLabelText("repoDetail.switchAria"));
    const item = await screen.findByText("dev");
    fireEvent.click(item);
    await waitFor(() => expect(onSwitch).toHaveBeenCalledWith("dev"));
  });

  it("枚举失败（502）：显示失败提示，手输兜底仍可用", async () => {
    mockBranches({ status: 502, body: { detail: "ls-remote 失败" } });
    const onSwitch = renderCombobox({ onSwitch: vi.fn() });
    fireEvent.click(screen.getByLabelText("repoDetail.switchAria"));
    await waitFor(() => expect(screen.getByText("repoDetail.branchLoadFailed")).toBeTruthy());
    const input = screen.getByPlaceholderText("repoDetail.branchSearch");
    fireEvent.change(input, { target: { value: "offline-branch" } });
    const fallback = await screen.findByText(/offline-branch/);
    fireEvent.click(fallback);
    await waitFor(() => expect(onSwitch).toHaveBeenCalledWith("offline-branch"));
  });
});
