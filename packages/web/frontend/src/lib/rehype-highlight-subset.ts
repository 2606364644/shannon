import { toText } from "hast-util-to-text";
import { createLowlight } from "lowlight";
import type { LanguageFn } from "lowlight";
import { visit } from "unist-util-visit";

/**
 * Vendored 精简版 rehype-highlight（spec §4.3，上游 MIT）。
 *
 * 与上游的差别只有一处：languages 必传、不引用 lowlight 的 `common` re-export——
 * 上游 fallback `settings.languages || common` 使 rollup 无法摇树，全量 common
 * 语法集（kotlin/objectivec/swift...35 个）永远进 report chunk。本实现只 import
 * `createLowlight`，未被引用的语法模块可被整体摇除。
 *
 * 能力裁剪：MarkdownView 只用固定语言表（不支持 detect/aliases/plainText/subset）。
 * 行为与上游对齐：language-* / lang-* class 解析、no-highlight 跳过、未注册语言
 * 降级为 vfile message（不抛错）、hljs class 前插、hast 子树替换。
 */

interface HElement {
  type: string;
  tagName: string;
  properties: { className?: unknown };
  position?: unknown;
  children: unknown[];
}

interface SubsetOptions {
  languages: Readonly<Record<string, LanguageFn>>;
  prefix?: string;
}

export default function rehypeHighlightSubset(options?: SubsetOptions) {
  const settings = options ?? { languages: {} };
  const prefix = settings.prefix;
  let name = "hljs";
  if (prefix) {
    const pos = prefix.indexOf("-");
    name = pos === -1 ? prefix : prefix.slice(0, pos);
  }
  const lowlight = createLowlight(settings.languages);

  return function (tree: unknown, file: { message: (...args: unknown[]) => unknown }) {
    visit(tree as Parameters<typeof visit>[0], "element", function (node, _, parent) {
      const el = node as HElement;
      const parentEl = parent as HElement | undefined;
      if (
        el.tagName !== "code" ||
        !parentEl ||
        parentEl.type !== "element" ||
        parentEl.tagName !== "pre"
      ) {
        return;
      }

      const lang = language(el);
      if (lang === false || !lang) return;

      if (!Array.isArray(el.properties.className)) el.properties.className = [];
      const classes = el.properties.className as unknown[];
      if (!classes.includes(name)) classes.unshift(name);

      const text = toText(el as never, { whitespace: "pre" });
      let result: { children: unknown[] };
      try {
        result = lowlight.highlight(lang, text, { prefix });
      } catch (error) {
        const cause = error as Error;
        if (/Unknown language/.test(cause.message)) {
          file.message("Cannot highlight as `" + lang + "`, it's not registered", {
            ancestors: [parentEl, el],
            cause,
            place: el.position,
            ruleId: "missing-language",
            source: "rehype-highlight-subset",
          });
          return;
        }
        throw cause;
      }
      if (result.children.length > 0) {
        el.children = result.children;
      }
    });
  };
}

/** 解析 code 节点的语言标记；`no-highlight` 显式返回 false。 */
function language(node: HElement): false | string | undefined {
  const list = node.properties.className;
  if (!Array.isArray(list)) return undefined;
  let name: string | undefined;
  for (const raw of list) {
    const value = String(raw);
    if (value === "no-highlight" || value === "nohighlight") return false;
    if (!name && value.slice(0, 5) === "lang-") name = value.slice(5);
    if (!name && value.slice(0, 9) === "language-") name = value.slice(9);
  }
  return name;
}
