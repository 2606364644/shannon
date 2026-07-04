import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ErrorState } from "./ErrorState";

describe("ErrorState", () => {
  it("渲染 message 与 role=alert", () => {
    render(<ErrorState message="加载失败" />);
    expect(screen.getByRole("alert")).toHaveTextContent("加载失败");
  });
  it("无 onRetry 不渲染重试按钮", () => {
    render(<ErrorState message="x" />);
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("有 onRetry 渲染重试按钮并触发", () => {
    const onRetry = vi.fn();
    render(<ErrorState message="x" onRetry={onRetry} />);
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
