import { createElement, type ReactNode } from "react";
import { createLowlight } from "lowlight";
import { HL_LANGS } from "./hljs-langs";

/**
 * 结构化报告代码块的手动高亮（VulnerabilityCard 的 PoC curl / Burp 报文 /
 * verify 命令 / 源码片段——它们不是 md，是裸文本字段，走不了 rehype 管线）。
 * 与 md 路径（rehype-highlight-subset）同源：同一份 HL_LANGS 语言子集 + lowlight，
 * token 类（.hljs-*）同一套，配 report.css 的代码主题配色。
 *
 * 降级立场：lang 未知 / 未注册 / 高亮抛错 → 原样纯文本（单色面板，无害）——
 * 高亮是增强不是前置条件，数据缺语言信息时报告照常可读。
 */

const lowlight = createLowlight(HL_LANGS);

/** lowlight 产出的 hast 子集（element/text 两类，properties 只有 className）。 */
interface HastNode {
  type: string;
  tagName?: string;
  properties?: { className?: unknown };
  value?: string;
  children?: HastNode[];
}

/** 把裸代码文本按语言 tokenize 成 React 节点数组（可直接作 <pre> 的 children）。 */
export function highlightCode(code: string, lang: string | null | undefined): ReactNode[] {
  if (!lang) return [code];
  try {
    const root = lowlight.highlight(lang, code);
    return root.children.map(hastToReact);
  } catch {
    // 未注册语言（lowlight 抛 Unknown language）等一切失败 → 原样文本
    return [code];
  }
}

function hastToReact(node: HastNode, i: number): ReactNode {
  if (node.type === "text") return node.value ?? "";
  if (node.type === "element") {
    const classes = Array.isArray(node.properties?.className)
      ? (node.properties!.className as unknown[]).map(String).join(" ")
      : "";
    // createElement（非 JSX）：本文件是 .ts 工具模块，esbuild 不解析 .ts 内 JSX
    return createElement(
      "span",
      { key: i, className: classes || undefined },
      (node.children ?? []).map(hastToReact),
    );
  }
  return null;
}

/** location / 文件名 → hljs 语言 id（按扩展名推断，覆盖 HL_LANGS 子集）。
 *  识别不出返回 null（调用方降级单色）。location 形如 `GradesController.java:fn:712:36`，
 *  先剥 `:行号/函数` 尾巴再取扩展名。 */
const EXT_LANG: Record<string, string> = {
  py: "python",
  js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript",
  ts: "typescript", tsx: "typescript",
  java: "java",
  sql: "sql",
  sh: "bash", bash: "bash", zsh: "bash",
  json: "json",
  yml: "yaml", yaml: "yaml",
  xml: "xml", html: "xml", htm: "xml", svg: "xml", jsp: "xml", vue: "xml",
  css: "css",
  ini: "ini", conf: "ini", cfg: "ini", env: "ini", properties: "ini", toml: "ini",
};

export function langFromPath(location: string): string | null {
  const stem = location.split(":")[0];
  const ext = /\.([A-Za-z0-9]+)$/.exec(stem)?.[1]?.toLowerCase();
  return (ext && EXT_LANG[ext]) || null;
}
