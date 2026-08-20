import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render } from "@testing-library/react";
import i18n from "@/i18n";
import { SafeEntries } from "../SafeEntries";
import type { SafeVector } from "@/api/types";

const vectors: SafeVector[] = [
  { subject: "req.query.tag", location: "app/list.ts:12", defense_mechanism: "参数化查询" },
  { subject: "req.body.bio", location: "app/profile.ts:30", defense_mechanism: null, render_context: "HTML 转义" },
];

describe("SafeEntries — 排查过的入口（spec §5 区 3）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));
  afterEach(() => i18n.changeLanguage("zh"));

  it("空态：无 safe_vectors → 不渲染（区隐藏）", () => {
    const { container } = render(<SafeEntries vectors={[]} />);
    expect(container.querySelector("[data-safe-section]")).toBeNull();
  });

  it("区头说明：「有起点、无危险终点」白话（不成树不成漏洞，证明扫过查过）", () => {
    const { container } = render(<SafeEntries vectors={vectors} />);
    const section = container.querySelector("[data-safe-section]")!;
    expect(section).toBeTruthy();
    expect(section.textContent ?? "").toContain("排查过的入口");
    expect(section.textContent ?? "").toContain("没有流向任何危险调用点");
    expect(section.textContent ?? "").toContain("有起点");
    expect(section.textContent ?? "").toContain("不成树");
  });

  it("safe_vectors 平铺：subject + 防护机制 + 位置", () => {
    const { container } = render(<SafeEntries vectors={vectors} />);
    const rows = container.querySelectorAll("[data-safe-vector]");
    expect(rows.length).toBe(2);
    expect(rows[0].textContent ?? "").toContain("req.query.tag");
    expect(rows[0].textContent ?? "").toContain("参数化查询"); // 防护机制
    expect(rows[0].textContent ?? "").toContain("app/list.ts:12"); // 位置
    // render_context 存在时展示
    expect(rows[1].textContent ?? "").toContain("HTML 转义");
  });

  it("i18n：切英文区头标题", () => {
    i18n.changeLanguage("en");
    const { container } = render(<SafeEntries vectors={vectors} />);
    expect(container.textContent ?? "").toContain("Checked entries");
  });
});
