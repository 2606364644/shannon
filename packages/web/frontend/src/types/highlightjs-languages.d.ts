/**
 * highlight.js 逐语言模块（highlight.js/lib/languages/*）无类型声明
 * （其 exports map 不为语言子路径提供 types 条目）——统一声明为 LanguageFn
 * 默认导出（与 lowlight/highlight.js 的运行时形状一致）。
 */
declare module "highlight.js/lib/languages/*" {
  import type { LanguageFn } from "lowlight";
  const language: LanguageFn;
  export default language;
}
