/** RefRangeInput（2026-09-04 MR 表单重排）：base⟷head 区间控件——swap 交换、
 *  就绪摘要（mono git range）、错误就近显示、testid 契约（mr-base-ref/mr-head-ref
 *  供 ScanNewPage MR 测试寻址）。 */
import { it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RefRangeInput } from "./RefRangeInput";

vi.mock("react-i18next", () => {
  const t = (k: string) => k;
  return { useTranslation: () => ({ t }) };
});

function setup(over: Partial<Parameters<typeof RefRangeInput>[0]> = {}) {
  const onBase = vi.fn();
  const onHead = vi.fn();
  const props = {
    base: "main", head: "feature/xss", onBase, onHead, error: null as string | null, ...over,
  };
  return { props, onBase, onHead, ...render(<RefRangeInput {...props} />) };
}

it("双输入位绑定 base/head（testid 契约 + label 关联）", () => {
  setup();
  expect((screen.getByTestId("mr-base-ref") as HTMLInputElement).value).toBe("main");
  expect((screen.getByTestId("mr-head-ref") as HTMLInputElement).value).toBe("feature/xss");
  expect(screen.getByLabelText("scan.mr.baseLabel")).toBe(screen.getByTestId("mr-base-ref"));
});

it("两端齐备 → 就绪摘要 base..head（mono git range）；缺一端不渲染", () => {
  const { rerender } = setup();
  expect(screen.getByTestId("mr-range-summary")).toHaveTextContent("main..feature/xss");
  rerender(<RefRangeInput base="main" head="" onBase={vi.fn()} onHead={vi.fn()} error={null} />);
  expect(screen.queryByTestId("mr-range-summary")).toBeNull();
});

it("swap 一键交换 base/head（填反救回）", () => {
  const { onBase, onHead } = setup();
  fireEvent.click(screen.getByTestId("mr-ref-swap"));
  expect(onBase).toHaveBeenCalledWith("feature/xss");
  expect(onHead).toHaveBeenCalledWith("main");
});

it("错误就近显示在控件内；就绪时摘要优先（错误不重复露脸）", () => {
  // 缺 head + 有错 → 错误文案显示
  const view = setup({ head: "", error: "请填写 base 与 head 引用" });
  expect(screen.getByText("请填写 base 与 head 引用")).toBeInTheDocument();
  expect(screen.queryByTestId("mr-range-summary")).toBeNull();
  // 补齐 head（错误仍在 props）→ 摘要出现、错误让位
  view.rerender(
    <RefRangeInput base="main" head="feature/xss" onBase={vi.fn()} onHead={vi.fn()} error="请填写 base 与 head 引用" />,
  );
  expect(screen.getByTestId("mr-range-summary")).toBeInTheDocument();
  expect(screen.queryByText("请填写 base 与 head 引用")).toBeNull();
});
