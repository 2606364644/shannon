import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { RepoCombobox } from "./RepoCombobox";
import type { Repo } from "@/api/types";

const REPOS: Repo[] = [
  {
    name: "frontend/admin",
    group: "frontend",
    source: { kind: "git", url: "https://gitlab.com/team-x/admin.git" },
    state: "ready",
  },
  {
    name: "frontend/my-app",
    group: "frontend",
    source: { kind: "git", url: "https://gitlab.com/team-y/myapp.git" },
    state: "ready",
  },
  {
    name: "plain-repo",
    group: null,
    source: { kind: "git", url: "https://github.com/ev/plain.git" },
    state: "ready",
  },
];

const baseProps = {
  repos: REPOS,
  placeholder: "选择仓库",
  searchPlaceholder: "搜索仓库...",
  emptyText: "无匹配仓库",
  ungroupedLabel: "未分组",
};

describe("RepoCombobox", () => {
  it("未选中时触发器显示 placeholder", () => {
    render(<RepoCombobox {...baseProps} value={null} onChange={() => {}} />);
    expect(screen.getByText("选择仓库")).toBeInTheDocument();
  });

  it("选中时触发器显示短名（basename）", () => {
    render(
      <RepoCombobox {...baseProps} value="frontend/admin" onChange={() => {}} />,
    );
    expect(screen.getByText("admin")).toBeInTheDocument();
  });

  it("展开后渲染分组标题与全部仓库短名", async () => {
    render(<RepoCombobox {...baseProps} value={null} onChange={() => {}} />);
    fireEvent.click(screen.getByText("选择仓库"));
    expect(await screen.findByText("frontend")).toBeInTheDocument();
    expect(await screen.findByText("未分组")).toBeInTheDocument();
    expect(screen.getByText("admin")).toBeInTheDocument();
    expect(screen.getByText("my-app")).toBeInTheDocument();
    expect(screen.getByText("plain-repo")).toBeInTheDocument();
  });

  it("输入查询只显示匹配项（不匹配项消失）", async () => {
    render(<RepoCombobox {...baseProps} value={null} onChange={() => {}} />);
    fireEvent.click(screen.getByText("选择仓库"));
    const input = await screen.findByPlaceholderText("搜索仓库...");
    fireEvent.change(input, { target: { value: "admin" } });
    await waitFor(() => {
      expect(screen.getByText("admin")).toBeInTheDocument();
      expect(screen.queryByText("my-app")).not.toBeInTheDocument();
      expect(screen.queryByText("plain-repo")).not.toBeInTheDocument();
    });
  });

  it("无匹配时显示空文案", async () => {
    render(<RepoCombobox {...baseProps} value={null} onChange={() => {}} />);
    fireEvent.click(screen.getByText("选择仓库"));
    const input = await screen.findByPlaceholderText("搜索仓库...");
    fireEvent.change(input, { target: { value: "zzz不存在的" } });
    expect(await screen.findByText("无匹配仓库")).toBeInTheDocument();
  });

  it("点击匹配项触发 onChange(完整 name) 并关闭", async () => {
    const onChange = vi.fn();
    render(<RepoCombobox {...baseProps} value={null} onChange={onChange} />);
    fireEvent.click(screen.getByText("选择仓库"));
    const item = await screen.findByText("admin");
    fireEvent.click(item);
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith("frontend/admin"),
    );
  });

  it("关联仓库项显示关联标记", async () => {
    const repos: Repo[] = [
      { name: "ftoa", group: null, source: { kind: "linked" }, state: "ready", linked: true },
    ];
    render(<RepoCombobox repos={repos} value={null} onChange={() => {}}
      placeholder="选" linkedLabel="关联" />);
    fireEvent.click(screen.getByText("选"));
    expect(await screen.findByText("ftoa")).toBeInTheDocument();
    expect(screen.getByText("关联")).toBeInTheDocument();
  });

  it("非关联仓库不显示关联标记", async () => {
    render(<RepoCombobox {...baseProps} value={null} onChange={() => {}} linkedLabel="关联" />);
    fireEvent.click(screen.getByText("选择仓库"));
    await screen.findByText("admin");
    expect(screen.queryByText("关联")).not.toBeInTheDocument();
  });

  // 取消选择（2026-09-04，MR 表单场景）：已选仓库可清空——用户先手选了仓库、
  // 后想改走 MR 链接导入路径时需要回到「未选」态。onClear 未传 = 零变化（其它调用方）。
  it("传入 onClear 且有选中值时点击 × 触发 onClear 且不打开下拉", () => {
    const onClear = vi.fn();
    render(
      <RepoCombobox
        {...baseProps}
        value="frontend/admin"
        onChange={() => {}}
        onClear={onClear}
        clearLabel="清除选择"
      />,
    );
    const clearBtn = screen.getByRole("button", { name: "清除选择" });
    fireEvent.click(clearBtn);
    expect(onClear).toHaveBeenCalledTimes(1);
    // 下拉未打开（搜索框不存在）——× 点击不得冒泡成 trigger
    expect(screen.queryByPlaceholderText("搜索仓库...")).not.toBeInTheDocument();
  });

  it("传入 onClear 但无选中值时不渲染 ×（无可清除内容）", () => {
    render(
      <RepoCombobox
        {...baseProps}
        value={null}
        onChange={() => {}}
        onClear={() => {}}
        clearLabel="清除选择"
      />,
    );
    expect(screen.queryByRole("button", { name: "清除选择" })).not.toBeInTheDocument();
  });

  it("未传 onClear 时选中态不渲染 ×（向后兼容）", () => {
    render(<RepoCombobox {...baseProps} value="frontend/admin" onChange={() => {}} />);
    expect(screen.queryByRole("button", { name: /clear|清除/i })).not.toBeInTheDocument();
  });
});
