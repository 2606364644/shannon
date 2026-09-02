import { describe, it, expect } from "vitest";
import { anchorPair, type Box } from "./topology-anchors";

/** 105×48 节点盒（编辑画布尺寸），以 (0,0) 为 from 基准。 */
const A: Box = { x: 0, y: 0, w: 105, h: 48 };
const box = (x: number, y: number): Box => ({ x, y, w: 105, h: 48 });

describe("anchorPair", () => {
  it("目标在右 → from 右缘中点 → to 左缘中点（原固定写死的形态）", () => {
    const r = anchorPair(A, box(300, 0));
    expect(r.from).toEqual({ x: 105, y: 24 });
    expect(r.to).toEqual({ x: 300, y: 24 });
  });

  it("目标在左 → from 左缘 → to 右缘（不穿节点、不反向折返）", () => {
    const r = anchorPair(A, box(-300, 0));
    expect(r.from).toEqual({ x: 0, y: 24 });
    expect(r.to).toEqual({ x: -195, y: 24 });
  });

  it("目标在正下方 → from 下缘中点 → to 上缘中点", () => {
    const r = anchorPair(A, box(0, 200));
    expect(r.from).toEqual({ x: 52.5, y: 48 });
    expect(r.to).toEqual({ x: 52.5, y: 200 });
  });

  it("目标在上方 → from 上缘中点 → to 下缘中点", () => {
    const r = anchorPair(A, box(0, -200));
    expect(r.from).toEqual({ x: 52.5, y: 0 });
    expect(r.to).toEqual({ x: 52.5, y: -152 });
  });

  it("斜向（右下）按主轴取水平缘，线段走两节点间空带", () => {
    const r = anchorPair(A, box(200, 120));
    expect(r.from.x).toBe(105);
    expect(r.to.x).toBe(200);
  });

  it("斜向（左上）按主轴取水平缘（|dx|>|dy| 时）", () => {
    const r = anchorPair(A, box(-200, -60));
    expect(r.from.x).toBe(0);
    expect(r.to.x).toBe(-95);
  });

  it("斜向（|dy|>|dx|）按垂直主轴取上下缘", () => {
    const r = anchorPair(A, box(40, -300));
    expect(r.from.y).toBe(0);
    expect(r.to.y).toBe(-252);
  });
});
