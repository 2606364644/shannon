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
