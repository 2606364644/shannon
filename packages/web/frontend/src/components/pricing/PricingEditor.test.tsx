import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { PricingEditor } from "./PricingEditor";
import type { PricingRow, Prices } from "@/api/pricing";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

const P = (input: number, output: number, cache_read: number, cache_creation = 0): Prices => ({
  input, output, cache_read, cache_creation,
});

const ROWS: PricingRow[] = [
  { model: "glm-5.2", prices: P(8, 28, 2), source: "builtin" },
  { model: "deepseek-v4-pro", prices: P(3, 6, 0.025), source: "profile_env" },
  { model: "glm-5.3", prices: P(9, 30, 2), source: "global" },
  { model: "glm-4.5-air", prices: P(0.9, 6, 0.16), source: "workspace" },
];

const BUILTIN: Record<string, Prices> = {
  "glm-5.2": P(8, 28, 2),
  "glm-4.5-air": P(0.8, 6, 0.16),
};

const onSave = vi.fn().mockResolvedValue(undefined);
const onClear = vi.fn().mockResolvedValue(undefined);

function inputFor(model: string, key: string) {
  return screen.getByTestId(`pricing-cell-${model}-${key}`);
}

beforeEach(() => {
  onSave.mockClear();
  onClear.mockClear();
});

describe("PricingEditor", () => {
  it("渲染来源徽章四态；只读态无操作列、无输入框", () => {
    render(
      <PricingEditor scope="global" currency="CNY" rows={ROWS}
        builtinDefaults={BUILTIN} canEdit={false} onSave={onSave} />,
    );
    expect(screen.getByTestId("pricing-source-glm-5.2").textContent).toBe("pricing.source.builtin");
    expect(screen.getByTestId("pricing-source-deepseek-v4-pro").textContent).toBe("pricing.source.profile_env");
    expect(screen.getByTestId("pricing-source-glm-5.3").textContent).toBe("pricing.source.global");
    expect(screen.getByTestId("pricing-source-glm-4.5-air").textContent).toBe("pricing.source.workspace");
    // 只读：无输入框 / 无保存按钮 / 无新增模型
    expect(screen.queryByTestId("pricing-save")).not.toBeInTheDocument();
    expect(screen.queryByTestId("pricing-add-row")).not.toBeInTheDocument();
    expect(screen.queryAllByRole("textbox").length).toBe(0);
    // 数字以文本呈现（tabular-nums 表格）
    expect(screen.getByTestId("pricing-readonly-glm-5.2-input").textContent).toBe("8");
  });

  it("编辑脏状态：改动后启用保存并出现脏提示；重置还原", async () => {
    render(
      <PricingEditor scope="global" currency="CNY" rows={ROWS}
        builtinDefaults={BUILTIN} canEdit onSave={onSave} />,
    );
    expect(screen.getByTestId("pricing-save")).toBeDisabled();
    fireEvent.change(inputFor("glm-5.2", "input"), { target: { value: "8.5" } });
    expect(screen.getByTestId("pricing-save")).toBeEnabled();
    expect(screen.getByTestId("pricing-dirty")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("pricing-reset"));
    expect(screen.getByTestId("pricing-save")).toBeDisabled();
    expect((inputFor("glm-5.2", "input") as HTMLInputElement).value).toBe("8");
  });

  it("非法值（负数 / 非数字 / 空）→ 行内错误 + 禁用保存", () => {
    render(
      <PricingEditor scope="global" currency="CNY" rows={ROWS}
        builtinDefaults={BUILTIN} canEdit onSave={onSave} />,
    );
    fireEvent.change(inputFor("glm-5.2", "input"), { target: { value: "-1" } });
    expect(screen.getByTestId("pricing-invalid-glm-5.2")).toBeInTheDocument();
    expect(screen.getByTestId("pricing-save")).toBeDisabled();
    fireEvent.change(inputFor("glm-5.2", "input"), { target: { value: "abc" } });
    expect(screen.getByTestId("pricing-invalid-glm-5.2")).toBeInTheDocument();
    fireEvent.change(inputFor("glm-5.2", "input"), { target: { value: "" } });
    expect(screen.getByTestId("pricing-save")).toBeDisabled();
    fireEvent.change(inputFor("glm-5.2", "input"), { target: { value: "8" } });
    expect(screen.getByTestId("pricing-save")).toBeDisabled(); // 值合法但回到原值 → 非脏
  });

  it("恢复默认：builtin 模型行（任意 source）一键回填内置价并进入保存 payload", async () => {
    // glm-5.3 在 builtin_defaults 中（同族模型）——source=global 也可拨回内置价
    const withDefault = { ...BUILTIN, "glm-5.3": P(8, 28, 2) };
    render(
      <PricingEditor scope="global" currency="CNY" rows={ROWS}
        builtinDefaults={withDefault} canEdit onSave={onSave} />,
    );
    fireEvent.change(inputFor("glm-5.3", "input"), { target: { value: "99" } });
    fireEvent.click(screen.getByTestId("pricing-restore-glm-5.3"));
    expect((inputFor("glm-5.3", "input") as HTMLInputElement).value).toBe("8");
    // 初始值 9 → 回填 8 仍是一次修改（脏）；恢复默认只回填不自动保存
    expect(screen.getByTestId("pricing-save")).toBeEnabled();
    // 直接保存：payload 含回填后的 glm-5.3 内置价，其余行原值随表提交
    fireEvent.click(screen.getByTestId("pricing-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const [, models] = onSave.mock.calls[0];
    expect(models["glm-5.3"]).toEqual({ ...P(8, 28, 2), currency: null });
    expect(models["glm-5.2"].output).toBe(28);
  });

  it("新增模型：归一后重复 id 拒绝；有效新行进入 payload", async () => {
    render(
      <PricingEditor scope="global" currency="CNY" rows={ROWS}
        builtinDefaults={BUILTIN} canEdit onSave={onSave} />,
    );
    fireEvent.click(screen.getByTestId("pricing-add-row"));
    const idInput = screen.getByTestId("pricing-new-model") as HTMLInputElement;
    fireEvent.change(idInput, { target: { value: "GLM-5.2[1m]" } }); // 归一后与 glm-5.2 冲突
    fireEvent.change(screen.getByTestId("pricing-cell-__new__-input"), { target: { value: "7" } });
    fireEvent.change(screen.getByTestId("pricing-cell-__new__-output"), { target: { value: "26" } });
    fireEvent.change(screen.getByTestId("pricing-cell-__new__-cache_read"), { target: { value: "1" } });
    fireEvent.change(screen.getByTestId("pricing-cell-__new__-cache_creation"), { target: { value: "0" } });
    expect(screen.getByTestId("pricing-dup-id")).toBeInTheDocument();
    expect(screen.getByTestId("pricing-save")).toBeDisabled();
    fireEvent.change(idInput, { target: { value: "glm-5.4" } });
    expect(screen.queryByTestId("pricing-dup-id")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("pricing-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const [currency, models] = onSave.mock.calls[0];
    expect(currency).toBe("CNY");
    expect(models["glm-5.4"]).toEqual({ ...P(7, 26, 1, 0), currency: null });
  });

  it("删除非 builtin 行：从 payload 移除；builtin 行无删除钮", async () => {
    render(
      <PricingEditor scope="global" currency="CNY" rows={ROWS}
        builtinDefaults={BUILTIN} canEdit onSave={onSave} />,
    );
    expect(screen.queryByTestId("pricing-delete-glm-5.2")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("pricing-delete-glm-5.3"));
    fireEvent.click(screen.getByTestId("pricing-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const [, models] = onSave.mock.calls[0];
    expect(models["glm-5.3"]).toBeUndefined();
    expect(models["glm-5.2"]).toBeDefined();
  });

  it("币种切换构成脏并进入 onSave currency", async () => {
    render(
      <PricingEditor scope="global" currency="CNY" rows={ROWS}
        builtinDefaults={BUILTIN} canEdit onSave={onSave} />,
    );
    fireEvent.click(screen.getByTestId("pricing-currency-USD"));
    expect(screen.getByTestId("pricing-save")).toBeEnabled();
    fireEvent.click(screen.getByTestId("pricing-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave.mock.calls[0][0]).toBe("USD");
  });

  it("列序 = 模型|输入|输出|缓存读取|缓存写入|…（输入/输出相邻靠前，对齐官方定价页）", () => {
    render(
      <PricingEditor scope="global" currency="CNY" rows={ROWS}
        builtinDefaults={BUILTIN} canEdit onSave={onSave} />,
    );
    const heads = screen.getAllByRole("columnheader").map((h) => h.textContent);
    // i18n mock（t = key）：列头文本即 key 序。缓存两档靠后成组，不再插在输入/输出中间。
    expect(heads).toEqual([
      "pricing.colModel", "pricing.col.input", "pricing.col.output",
      "pricing.col.cache_read", "pricing.col.cache_creation",
      "pricing.colCurrency", "pricing.colSource", "pricing.colActions",
    ]);
  });

  it("行级币种：切换 USD 成脏 + 进 payload；「默认」钮清除回跟随（null）", async () => {
    const rows: PricingRow[] = [
      { model: "glm-5.2", prices: P(8, 28, 2), source: "builtin" },
      { model: "m-usd", prices: P(1, 2, 0.5), source: "global", currency: "USD" },
    ];
    render(
      <PricingEditor scope="global" currency="CNY" rows={rows}
        builtinDefaults={BUILTIN} canEdit onSave={onSave} />,
    );
    // 显式行：USD 钮初始 pressed；切换 glm-5.2 到 USD → 脏
    expect(screen.getByTestId("pricing-row-currency-glm-5.2-USD").getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(screen.getByTestId("pricing-row-currency-glm-5.2-USD"));
    expect(screen.getByTestId("pricing-save")).toBeEnabled();
    fireEvent.click(screen.getByTestId("pricing-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const [, models] = onSave.mock.calls[0];
    expect(models["glm-5.2"].currency).toBe("USD");
    expect(models["m-usd"].currency).toBe("USD");   // 显式行保持
    // 「默认」钮在显式行上清除覆盖 → null ≠ 初始 USD → 脏，payload 回跟随
    //（glm-5.2 不能用于此断言：其初始即 null，切回默认 = 回原状不脏）
    fireEvent.click(screen.getByTestId("pricing-row-currency-m-usd-default"));
    fireEvent.click(screen.getByTestId("pricing-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(2));
    expect(onSave.mock.calls[1][1]["m-usd"].currency).toBeNull();
  });

  it("只读态：行币种为符号文本；null 行显示表默认符号且带跟随提示", () => {
    const rows: PricingRow[] = [
      { model: "m-usd", prices: P(1, 2, 0.5), source: "global", currency: "USD" },
      { model: "glm-5.2", prices: P(8, 28, 2), source: "builtin" },
    ];
    render(
      <PricingEditor scope="global" currency="CNY" rows={rows}
        builtinDefaults={BUILTIN} canEdit={false} onSave={onSave} />,
    );
    expect(screen.getByTestId("pricing-row-currency-m-usd").textContent).toBe("$");
    // null（跟随表级 CNY）→ 显示 ¥，title 提示跟随默认
    const follow = screen.getByTestId("pricing-row-currency-glm-5.2");
    expect(follow.textContent).toBe("¥");
    expect(follow.getAttribute("title")).toBe("pricing.currencyDefault");
  });

  it("onClear 提供时才渲染清除按钮（确认由挂载点管）", () => {
    const { rerender } = render(
      <PricingEditor scope="global" currency="CNY" rows={ROWS}
        builtinDefaults={BUILTIN} canEdit onSave={onSave} />,
    );
    expect(screen.queryByTestId("pricing-clear")).not.toBeInTheDocument();
    rerender(
      <PricingEditor scope="workspace" currency="CNY" rows={ROWS}
        builtinDefaults={BUILTIN} canEdit onSave={onSave} onClear={onClear} hasOverride />,
    );
    fireEvent.click(screen.getByTestId("pricing-clear"));
    waitFor(() => expect(onClear).toHaveBeenCalledTimes(1));
  });
});
