// cost 币种渲染 helper（spec 2026-07-09 per-profile cost 定价）。
// 后端 cost_usd 字段值 = cost_currency 币种的金额；前端按 cost_currency 选符号（¥/$）。

export const CURRENCY_SYMBOL: Record<string, string> = { CNY: "¥", USD: "$" };

export function currencySymbol(c?: string | null): string {
  return (c && CURRENCY_SYMBOL[c]) || "$";
}

export function fmtCost(v: number | null | undefined, currency?: string | null): string {
  return v == null ? "—" : `${currencySymbol(currency)}${v.toFixed(2)}`;
}
