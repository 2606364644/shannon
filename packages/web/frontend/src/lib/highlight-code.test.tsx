import { describe, it, expect } from "vitest";
import type { ReactElement } from "react";
import { highlightCode, langFromPath } from "./highlight-code";

/** 收集节点树里所有 React 元素的 className（断言 hljs token 类存在）。 */
function collectClassNames(nodes: unknown, acc: string[] = []): string[] {
  for (const n of Array.isArray(nodes) ? nodes : [nodes]) {
    if (typeof n === "object" && n !== null && "type" in (n as object)) {
      const el = n as ReactElement<{ className?: string; children?: unknown }>;
      if (el.props?.className) acc.push(el.props.className);
      collectClassNames(el.props?.children, acc);
    }
  }
  return acc;
}

describe("highlightCode", () => {
  it("sql 代码产出 hljs token span（keyword/string 着色类）", () => {
    const nodes = highlightCode("SELECT * FROM users WHERE id = '1'", "sql");
    const classes = collectClassNames(nodes);
    expect(classes.join(" ")).toContain("hljs-keyword");
    expect(classes.join(" ")).toContain("hljs-string");
  });

  it("bash 代码：URL/参数照常 tokenize（curl PoC 主场景）", () => {
    const nodes = highlightCode("curl -X GET 'http://localhost:4000/api/memos'", "bash");
    const classes = collectClassNames(nodes);
    expect(classes.length).toBeGreaterThan(0);
  });

  it("http 代码：Burp 原始报文 method/头部产出 token", () => {
    const nodes = highlightCode("GET /memos HTTP/1.1\nHost: localhost\n", "http");
    expect(collectClassNames(nodes).join(" ")).not.toBe("");
  });

  it("lang=null → 原样纯文本（无 span 包裹）", () => {
    const nodes = highlightCode("plain text", null);
    expect(nodes).toEqual(["plain text"]);
  });

  it("未注册语言（如 kotlin）→ 降级原样文本，不抛错", () => {
    const nodes = highlightCode("val x = 1", "kotlin");
    expect(nodes).toEqual(["val x = 1"]);
  });

  it("空串安全（lowlight 对空串产空数组，不抛错）", () => {
    expect(highlightCode("", "sql")).toEqual([]);
  });
});

describe("langFromPath", () => {
  it("从 location 提取扩展名映射语言（含 :行号/函数 尾巴）", () => {
    expect(langFromPath("/app/routes.py:42")).toBe("python");
    expect(langFromPath("GradesController.java:handler:712:36")).toBe("java");
    expect(langFromPath("src/api/user.ts:10")).toBe("typescript");
    expect(langFromPath("db/query.sql")).toBe("sql");
    expect(langFromPath("conf/app.yml")).toBe("yaml");
    expect(langFromPath("index.html:12")).toBe("xml");
  });

  it("未识别扩展名 → null（渲染层降级单色）", () => {
    expect(langFromPath("README.txt")).toBeNull();
    expect(langFromPath("Makefile")).toBeNull();
    expect(langFromPath("")).toBeNull();
  });
});
