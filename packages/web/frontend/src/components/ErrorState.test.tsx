import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import i18n from "@/i18n";
import { ErrorState } from "./ErrorState";

describe("ErrorState", () => {
  beforeEach(() => i18n.changeLanguage("zh"));
  afterEach(() => i18n.changeLanguage("zh"));

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

  it("i18n: 切英文 retry 按钮文案为 Retry", () => {
    i18n.changeLanguage("en");
    render(<ErrorState message="x" onRetry={() => {}} />);
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
