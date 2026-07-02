import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { YamlEditor } from "./YamlEditor";

vi.mock("@monaco-editor/react", () => ({
  default: ({ value, onChange }: { value: string; onChange?: (v: string) => void }) => (
    <textarea data-testid="monaco" value={value} onChange={(e) => onChange?.(e.target.value)} />
  ),
}));

describe("YamlEditor", () => {
  it("合法 yaml → onError 不触发", () => {
    const onError = vi.fn();
    render(<YamlEditor value={"repos:\n  a:\n    url: x"} onChange={() => {}} onError={onError} />);
    expect(onError).not.toHaveBeenCalled();
  });
  it("非法 yaml → onError 触发", () => {
    const onError = vi.fn();
    render(<YamlEditor value={"repos: [unclosed"} onChange={() => {}} onError={onError} />);
    expect(onError).toHaveBeenCalledWith(expect.any(String));
  });
  it("Monaco 挂载 YAML + 当前 value", () => {
    render(<YamlEditor value={"key: val"} onChange={() => {}} onError={() => {}} />);
    const ta = screen.getByTestId("monaco") as HTMLTextAreaElement;
    expect(ta.value).toBe("key: val");
  });
  it("编辑 Monaco → onChange 透传", () => {
    const onChange = vi.fn();
    render(<YamlEditor value={"a: 1"} onChange={onChange} onError={() => {}} />);
    fireEvent.change(screen.getByTestId("monaco"), { target: { value: "a: 2" } });
    expect(onChange).toHaveBeenCalledWith("a: 2");
  });
  it("非法 → 合法：onError 收到空串恢复", () => {
    const onError = vi.fn();
    const { rerender } = render(<YamlEditor value={"bad: ["} onChange={() => {}} onError={onError} />);
    expect(onError).toHaveBeenCalledWith(expect.any(String));
    onError.mockClear();
    rerender(<YamlEditor value={"good: ok"} onChange={() => {}} onError={onError} />);
    expect(onError).toHaveBeenCalledWith("");
  });
});
