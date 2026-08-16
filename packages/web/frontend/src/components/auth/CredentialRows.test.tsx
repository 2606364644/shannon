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

  it("内置角色 chips：渲染 超管/管理员/用户；当前角色命中时 aria-pressed", () => {
    render(<CredentialRows value={one} onChange={() => {}} allowMulti={false} />);
    expect(screen.getByRole("button", { name: "超管" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "管理员" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "用户" })).toHaveAttribute("aria-pressed", "false");
  });

  it("点内置角色 chip → onChange 填入对应 role 值（可多次切换）", () => {
    const onChange = vi.fn();
    const { rerender } = render(<CredentialRows value={one} onChange={onChange} allowMulti={false} />);
    fireEvent.click(screen.getByRole("button", { name: "超管" }));
    expect(onChange).toHaveBeenCalledWith([{ ...one[0], role: "superadmin" }]);
    // 受控组件：chip 高亮跟随 value（rerender 模拟父级回填）
    rerender(<CredentialRows
      value={[{ ...one[0], role: "superadmin" }]} onChange={() => {}} allowMulti={false} />);
    expect(screen.getByRole("button", { name: "超管" })).toHaveAttribute("aria-pressed", "true");
  });

  it("特殊角色（如审计管理员）不走预设：输入框手输仍可改 role", () => {
    const onChange = vi.fn();
    const audit: CredentialDraft[] = [{ role: "auditor", username: "a", password: "pw" }];
    render(<CredentialRows value={audit} onChange={onChange} allowMulti={false} />);
    // 无 chip 命中 + 输入框保留原值可继续编辑
    expect((screen.getByLabelText("角色") as HTMLInputElement).value).toBe("auditor");
    ["超管", "管理员", "用户"].forEach((n) =>
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
