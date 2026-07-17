import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { ErrorBoundary } from "./ErrorBoundary";

const Throw = ({ msg }: { msg: string }) => {
  throw new Error(msg);
};

describe("ErrorBoundary", () => {
  // 子组件抛错时 React 会 console.error 打印预期错误堆栈(噪音),统一抑制。
  let spy: ReturnType<typeof vi.spyOn>;
  afterEach(() => spy?.mockRestore());

  it("正常子组件直通渲染(无误捕)", () => {
    render(
      <ErrorBoundary>
        <div>fine</div>
      </ErrorBoundary>,
    );
    expect(screen.getByText("fine")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("子组件抛错时捕获并显 fallback(role=alert),不向上冒泡", () => {
    spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <Throw msg="boom" />
      </ErrorBoundary>,
    );
    // 捕获成功 = fallback 渲染(role=alert);若未捕获,render 会向上抛错致整个测试失败。
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("key 变化(切 tab)时重建并 reset,正常子组件重新渲染", () => {
    spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { rerender } = render(
      <ErrorBoundary key="bad">
        <Throw msg="boom" />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    // 切到正常 tab(key 变 → ErrorBoundary 卸载重建 → hasError 重置)
    rerender(
      <ErrorBoundary key="good">
        <div>ok content</div>
      </ErrorBoundary>,
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("ok content")).toBeInTheDocument();
  });
});
