import rehypeHighlightSubset from "@/lib/rehype-highlight-subset";
import bash from "highlight.js/lib/languages/bash";
import json from "highlight.js/lib/languages/json";
import python from "highlight.js/lib/languages/python";
import javascript from "highlight.js/lib/languages/javascript";
import typescript from "highlight.js/lib/languages/typescript";
import java from "highlight.js/lib/languages/java";
import sql from "highlight.js/lib/languages/sql";
import http from "highlight.js/lib/languages/http";
import yaml from "highlight.js/lib/languages/yaml";
import xml from "highlight.js/lib/languages/xml";
import ini from "highlight.js/lib/languages/ini";
import css from "highlight.js/lib/languages/css";

/**
 * 语言子集（spec §4.3）：经 vendored 精简插件（rehype-highlight-subset）注册，
 * 未列出的语法不进 bundle（上游 rehype-highlight 的 common fallback 不可摇树）。
 * 报告页全路径共享（MarkdownView md 降级 / RichText 结构化叙事 / highlight-code
 * 结构化代码块），单一事实源——扩语言只改这里。
 */
export const HL_LANGS = { bash, json, python, javascript, typescript, java, sql, http, yaml, xml, ini, css };

/** 嵌套元组形态（react-markdown 约定）：rehypePlugins 数组的单个元素 = [plugin, options]。 */
export const HIGHLIGHT_PLUGIN = [[rehypeHighlightSubset, { languages: HL_LANGS }]] as const;
