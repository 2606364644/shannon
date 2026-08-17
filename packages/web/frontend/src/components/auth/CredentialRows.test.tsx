import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import i18n from "@/i18n";
import { CredentialRows, type CredentialDraft } from "./CredentialRows";

beforeEach(() => i18n.changeLanguage("zh"));

const one: CredentialDraft[] = [
  { role: "admin", username: "a", password: "pw" },
];

describe("CredentialRows", () => {
  it("渲染 value 行的 role/username/password", () => {
    render(<CredentialRows value={one} onChange={() => {}} allowMulti={false} />);
    expect((screen.getByLabelText("角色") as HTMLInputElement).value).toBe("admin");
    expect((screen.getByLabelText("用户名") as HTMLInputElement).value).toBe("a");
    expect((screen.getByLabelText("密码") as HTMLInputElement).value).toBe("pw");
  });

  it("编辑字段调 onChange 更新对应 draft（不改其他行）", () => {
    const onChange = vi.fn();
    render(<CredentialRows value={one} onChange={onChange} allowMulti={false} />);
    fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "b" } });
    expect(onChange).toHaveBeenCalledWith([{ ...one[0], username: "b" }]);
  });

  it("内置角色 chips：渲染 管理员/用户；当前角色命中时 aria-pressed", () => {
    render(<CredentialRows value={one} onChange={() => {}} allowMulti={false} />);
    expect(screen.getByRole("button", { name: "管理员" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "用户" })).toHaveAttribute("aria-pressed", "false");
  });

  it("点内置角色 chip → onChange 填入对应 role 值（可多次切换）", () => {
    const onChange = vi.fn();
    const { rerender } = render(<CredentialRows value={one} onChange={onChange} allowMulti={false} />);
    fireEvent.click(screen.getByRole("button", { name: "用户" }));
    expect(onChange).toHaveBeenCalledWith([{ ...one[0], role: "user" }]);
    // 受控组件：chip 高亮跟随 value（rerender 模拟父级回填）
    rerender(<CredentialRows
      value={[{ ...one[0], role: "user" }]} onChange={() => {}} allowMulti={false} />);
    expect(screen.getByRole("button", { name: "用户" })).toHaveAttribute("aria-pressed", "true");
  });

  it("特殊角色（如审计管理员）不走预设：输入框手输仍可改 role", () => {
    const onChange = vi.fn();
    const audit: CredentialDraft[] = [{ role: "auditor", username: "a", password: "pw" }];
    render(<CredentialRows value={audit} onChange={onChange} allowMulti={false} />);
    // 无 chip 命中 + 输入框保留原值可继续编辑
    expect((screen.getByLabelText("角色") as HTMLInputElement).value).toBe("auditor");
    ["管理员", "用户"].forEach((n) =>
      expect(screen.getByRole("button", { name: n })).toHaveAttribute("aria-pressed", "false"));
    fireEvent.change(screen.getByLabelText("角色"), { target: { value: "审计管理员" } });
    expect(onChange).toHaveBeenCalledWith([{ ...audit[0], role: "审计管理员" }]);
  });

  it("allowMulti：点「+ 添加角色」追加一行空 draft", () => {
    const onChange = vi.fn();
    render(<CredentialRows value={one} onChange={onChange} allowMulti />);
    fireEvent.click(screen.getByRole("button", { name: /添加角色/ }));
    expect(onChange).toHaveBeenCalledWith([
      one[0],
      expect.objectContaining({ role: "", username: "", password: "" }),
    ]);
  });

  it("allowMulti + 多行：删该行；仅 1 行时不显删除按钮", () => {
    const onChange = vi.fn();
    const two: CredentialDraft[] = [
      one[0],
      { role: "user", username: "u", password: "p" },
    ];
    const { rerender } = render(
      <CredentialRows value={two} onChange={onChange} allowMulti />);
    // 2 行 → 删除按钮存在；点第一个删除第一行（无 lockFirstRow，任意行可删）
    fireEvent.click(screen.getAllByRole("button", { name: /删除该角色/ })[0]);
    expect(onChange).toHaveBeenCalledWith([two[1]]);
    // 仅 1 行 → 无删除按钮
    rerender(<CredentialRows value={one} onChange={() => {}} allowMulti />);
    expect(screen.queryByRole("button", { name: /删除该角色/ })).toBeNull();
  });

  it("lockFirstRow：首行（primary）不显删除按钮，仅附加角色可删", () => {
    const onChange = vi.fn();
    const two: CredentialDraft[] = [
      { role: "admin", username: "a", password: "pw" },
      { role: "user", username: "u", password: "p" },
    ];
    const { rerender } = render(
      <CredentialRows value={two} onChange={onChange} allowMulti lockFirstRow />);
    // 2 行 + lockFirstRow：首行（index 0）无删除按钮，仅附加角色（index 1）有 → 共 1 个
    expect(screen.getAllByRole("button", { name: /删除该角色/ })).toHaveLength(1);
    // 删附加角色 → 回到仅 primary
    fireEvent.click(screen.getByRole("button", { name: /删除该角色/ }));
    expect(onChange).toHaveBeenCalledWith([two[0]]);
    // 仅 primary → 无删除按钮
    rerender(<CredentialRows value={one} onChange={() => {}} allowMulti lockFirstRow />);
    expect(screen.queryByRole("button", { name: /删除该角色/ })).toBeNull();
  });
});

describe("CredentialRows 编辑态密码（hasPassword 折叠交互）", () => {
  const saved: CredentialDraft[] = [
    { id: "cred_1", role: "admin", username: "a", password: "", hasPassword: true },
  ];

  it("已存密码：不渲染密码输入框，显示 •••• 与「修改」按钮", () => {
    render(<CredentialRows value={saved} onChange={() => {}} allowMulti />);
    expect(screen.queryByLabelText("密码")).toBeNull();
    expect(screen.getByText("••••")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "修改" })).toBeInTheDocument();
  });

  it("点「修改」→ onChange 置 pwEditing；展开后占位「输入新密码」+「取消」", () => {
    const onChange = vi.fn();
    const { rerender } = render(<CredentialRows value={saved} onChange={onChange} allowMulti />);
    fireEvent.click(screen.getByRole("button", { name: "修改" }));
    expect(onChange).toHaveBeenCalledWith([{ ...saved[0], pwEditing: true }]);
    // 受控组件：rerender 模拟父级回填展开态
    const expanded: CredentialDraft[] = [{ ...saved[0], pwEditing: true }];
    rerender(<CredentialRows value={expanded} onChange={onChange} allowMulti />);
    const pw = screen.getByLabelText("密码") as HTMLInputElement;
    expect(pw.placeholder).toBe("输入新密码");
    expect(pw.value).toBe("");
    expect(screen.getByRole("button", { name: "取消" })).toBeInTheDocument();
    fireEvent.change(pw, { target: { value: "newpw" } });
    expect(onChange).toHaveBeenCalledWith([{ ...saved[0], pwEditing: true, password: "newpw" }]);
  });

  it("展开后点「取消」→ 收回输入框并清空已输入（回到 •••• 折叠态）", () => {
    const onChange = vi.fn();
    const typed: CredentialDraft[] = [{ ...saved[0], pwEditing: true, password: "newpw" }];
    const { rerender } = render(<CredentialRows value={typed} onChange={onChange} allowMulti />);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onChange).toHaveBeenCalledWith([{ ...saved[0], pwEditing: false, password: "" }]);
    rerender(<CredentialRows value={saved} onChange={() => {}} allowMulti />);
    expect(screen.queryByLabelText("密码")).toBeNull();
    expect(screen.getByRole("button", { name: "修改" })).toBeInTheDocument();
  });

  it("编辑行从未存过密码（hasPassword 缺省）：正常输入框，占位「未设置」且无取消按钮", () => {
    const unset: CredentialDraft[] = [{ id: "cred_2", role: "user", username: "u", password: "" }];
    render(<CredentialRows value={unset} onChange={() => {}} allowMulti />);
    const pw = screen.getByLabelText("密码") as HTMLInputElement;
    expect(pw.placeholder).toBe("未设置");
    expect(screen.queryByRole("button", { name: "取消" })).toBeNull();
  });

  it("新建行（无 id 无 hasPassword）：输入框无占位无取消——扫描页/新建不受影响", () => {
    render(<CredentialRows value={one} onChange={() => {}} allowMulti />);
    const pw = screen.getByLabelText("密码") as HTMLInputElement;
    expect(pw.value).toBe("pw");
    expect(pw.placeholder).toBe("");
    expect(screen.queryByRole("button", { name: "取消" })).toBeNull();
  });
});
