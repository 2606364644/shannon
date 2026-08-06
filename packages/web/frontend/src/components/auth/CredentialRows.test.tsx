import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import i18n from "@/i18n";
import { CredentialRows, type CredentialDraft } from "./CredentialRows";

beforeEach(() => i18n.changeLanguage("zh"));

const one: CredentialDraft[] = [
  { role: "admin", username: "a", password: "pw", totpSecret: "" },
];

describe("CredentialRows", () => {
  it("渲染 value 行的 role/username/password", () => {
    render(<CredentialRows value={one} onChange={() => {}} allowMulti={false} showTotp={false} />);
    expect((screen.getByLabelText("角色") as HTMLInputElement).value).toBe("admin");
    expect((screen.getByLabelText("用户名") as HTMLInputElement).value).toBe("a");
    expect((screen.getByLabelText("密码") as HTMLInputElement).value).toBe("pw");
  });

  it("编辑字段调 onChange 更新对应 draft（不改其他行）", () => {
    const onChange = vi.fn();
    render(<CredentialRows value={one} onChange={onChange} allowMulti={false} showTotp={false} />);
    fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "b" } });
    expect(onChange).toHaveBeenCalledWith([{ ...one[0], username: "b" }]);
  });

  it("allowMulti：点「+ 添加角色」追加一行空 draft", () => {
    const onChange = vi.fn();
    render(<CredentialRows value={one} onChange={onChange} allowMulti showTotp={false} />);
    fireEvent.click(screen.getByRole("button", { name: /添加角色/ }));
    expect(onChange).toHaveBeenCalledWith([
      one[0],
      expect.objectContaining({ role: "", username: "", password: "", totpSecret: "" }),
    ]);
  });

  it("allowMulti + 多行：删该行；仅 1 行时不显删除按钮", () => {
    const onChange = vi.fn();
    const two: CredentialDraft[] = [
      one[0],
      { role: "user", username: "u", password: "p", totpSecret: "" },
    ];
    const { rerender } = render(
      <CredentialRows value={two} onChange={onChange} allowMulti showTotp={false} />);
    // 2 行 → 删除按钮存在；点第一个删除第一行
    fireEvent.click(screen.getAllByRole("button", { name: /删除该角色/ })[0]);
    expect(onChange).toHaveBeenCalledWith([two[1]]);
    // 仅 1 行 → 无删除按钮
    rerender(<CredentialRows value={one} onChange={() => {}} allowMulti showTotp={false} />);
    expect(screen.queryByRole("button", { name: /删除该角色/ })).toBeNull();
  });

  it("showTotp 控制二步验证字段显隐", () => {
    const { rerender } = render(
      <CredentialRows value={one} onChange={() => {}} allowMulti={false} showTotp={false} />);
    expect(screen.queryByLabelText(/TOTP/)).toBeNull();
    rerender(<CredentialRows value={one} onChange={() => {}} allowMulti={false} showTotp />);
    expect(screen.getByLabelText(/TOTP/)).toBeInTheDocument();
  });

  it("showTotp：编辑 totp 字段回写 totpSecret", () => {
    const onChange = vi.fn();
    render(<CredentialRows value={one} onChange={onChange} allowMulti={false} showTotp />);
    fireEvent.change(screen.getByLabelText(/TOTP/), { target: { value: "T" } });
    expect(onChange).toHaveBeenCalledWith([{ ...one[0], totpSecret: "T" }]);
  });
});
