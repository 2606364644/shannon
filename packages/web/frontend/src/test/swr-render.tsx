import { render } from "@testing-library/react";
import type { RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { SWRConfig } from "swr";

/** SWR 组件测试渲染：每次 render 独立 cache（SWR 全局缓存跨测试会泄漏数据与状态）。 */
export function renderWithSwr(ui: ReactElement, options?: RenderOptions) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <SWRConfig value={{ provider: () => new Map() }}>{children}</SWRConfig>
  );
  return render(ui, { wrapper, ...options });
}
