// 真实数据几何守卫：本机存在指定扫描的 dataflow_view.json 时渲染剪枝树，
// 对所有 SVG 文本元素用组件同款宽度模型算 bbox，两两相交检测——锁「重叠」命题
// （2026-08-21 requirement-sec-review 真实数据回归：11 棵 llm: 树全量不相交）。
// 文件不存在（CI / 其他环境）自动跳过；换新扫描数据改 REAL 路径即可。
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { existsSync, readFileSync } from "node:fs";
import { PruningTreeFig } from "../PruningTreeFig";
import type { DataflowView } from "@/api/types";

const REAL =
  "/root/ft-codescan/workspaces/__legacy__/scans/requirement-sec-review-20260821-044018/deliverables/whitebox/intermediate/dataflow_view.json";

function textWidthPx(s: string): number {
  let w = 0;
  for (const ch of s) w += ch.charCodeAt(0) > 0xff ? 10 : 5.6;
  return w;
}

interface Box {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  tag: string;
}

describe.skipIf(!existsSync(REAL))("真实数据几何验证（守卫：有真实数据才跑）", () => {
  it("全部文本标签 bbox 两两不相交", () => {
    const view = JSON.parse(readFileSync(REAL, "utf-8")) as DataflowView;
    expect(view.trees.length).toBeGreaterThan(0);
    const { container } = render(<PruningTreeFig trees={view.trees} />);
    const svgs = container.querySelectorAll("svg");
    const overlapPairs: string[] = [];
    svgs.forEach((svg) => {
      const boxes: Box[] = [];
      svg.querySelectorAll(
        "text[data-node-label], text[data-source-label], text[data-source-meta], text[data-sink-label], text[data-sameline-label], text[data-pubfunc]",
      ).forEach((text) => {
        // 累加最近带 transform 的祖先 g 的平移量
        let tx = 0;
        let ty = 0;
        let el: Element | null = text;
        while (el && el !== svg) {
          const tr = el.getAttribute("transform");
          if (tr) {
            const m = tr.match(/translate\(([-\d.]+)[ ,]+([-\d.]+)\)/);
            if (m) {
              tx += parseFloat(m[1]);
              ty += parseFloat(m[2]);
            }
          }
          el = el.parentElement;
        }
        const anchor = text.getAttribute("textAnchor") ?? text.getAttribute("text-anchor") ?? "middle";
        const fx = parseFloat(text.getAttribute("x") ?? "0");
        const fy = parseFloat(text.getAttribute("y") ?? "0");
        const fs = 10;
        const pushBox = (content: string, y: number) => {
          const w = textWidthPx(content);
          const x1 = anchor === "middle" ? tx + fx - w / 2 : tx + fx;
          boxes.push({ x1, y1: ty + y - fs, x2: x1 + w, y2: ty + y + 2, tag: content.slice(0, 22) });
        };
        // tspan 分行建模：每个 tspan 一行（y = 基线 + 累计 dy），无 tspan 按单行
        const tspans = text.querySelectorAll("tspan");
        if (tspans.length > 0) {
          let curY = fy;
          tspans.forEach((ts, i) => {
            const content = ts.textContent ?? "";
            if (!content) return;
            const dy = parseFloat(ts.getAttribute("dy") ?? "0");
            curY = i === 0 ? fy : curY + dy;
            pushBox(content, curY);
          });
          return;
        }
        const content = text.textContent ?? "";
        if (!content) return;
        pushBox(content, fy);
      });
      for (let i = 0; i < boxes.length; i++) {
        for (let j = i + 1; j < boxes.length; j++) {
          const a = boxes[i];
          const b = boxes[j];
          const ix = Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1);
          const iy = Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1);
          if (ix > 2 && iy > 2) overlapPairs.push(`"${a.tag}" × "${b.tag}" (ix=${ix.toFixed(0)} iy=${iy.toFixed(0)})`);
        }
      }
    });
    expect(overlapPairs, overlapPairs.join("\n")).toEqual([]);
  });
});
