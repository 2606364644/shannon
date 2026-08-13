// MappingRows 虚拟滚动路径回归（对齐 LogStream.test.tsx 虚拟阈值断言范式）。
// 现有 HostProfilesPage.test.tsx 只覆盖普通模式（1 行）；此文件专验 >100 条走 react-window。
import { describe, it, expect } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
import { MappingRows, type MappingDraft } from "./MappingRows";

function renderRows(value: MappingDraft[], onChange: (next: MappingDraft[]) => void = () => {}) {
  return render(
    <MemoryRouter>
      <MappingRows value={value} onChange={onChange} />
    </MemoryRouter>,
  );
}

describe("MappingRows 虚拟滚动", () => {
  it(">100 条切 react-window，仅渲染可视区行（DOM 行数 < 总条数）", () => {
    i18n.changeLanguage("zh");
    const big: MappingDraft[] = Array.from({ length: 1200 }, (_, i) => ({
      ip: `10.0.${Math.floor(i / 256)}.${i % 256}`,
      host: `host-${i}.internal`,
    }));
    const { container } = renderRows(big);
    // FixedSizeList 渲染后：只有可视区 + overscan 行进 DOM，远少于 1200。
    const ipInputs = container.querySelectorAll("input[data-hm-ip]");
    expect(ipInputs.length).toBeLessThan(big.length);
    expect(ipInputs.length).toBeGreaterThan(0);
    // 计数 chip 仍显示总数（非 DOM 行数）
    const count = container.querySelector(".tabular-nums");
    expect(count?.textContent).toContain("1,200");
  });

  it("≤100 条走普通模式（无 FixedSizeList，全部行进 DOM）", () => {
    i18n.changeLanguage("zh");
    const small: MappingDraft[] = Array.from({ length: 50 }, (_, i) => ({
      ip: `10.0.0.${i}`,
      host: `h${i}.test`,
    }));
    const { container } = renderRows(small);
    // 普通模式：50 行全部渲染（FixedSizeList 未启用）
    expect(container.querySelectorAll("input[data-hm-ip]").length).toBe(50);
    expect(container.querySelector(".tabular-nums")?.textContent).toContain("50");
  });

  it("虚拟模式下编辑某行值写回 onChange（受控交互闭环）", () => {
    i18n.changeLanguage("zh");
    const big: MappingDraft[] = Array.from({ length: 150 }, (_, i) => ({
      ip: `10.0.0.${i}`,
      host: `h${i}.test`,
    }));
    let captured: MappingDraft[] = big;
    const onChange = (next: MappingDraft[]) => { captured = next; };
    const { container } = renderRows(big, onChange);
    const firstInput = container.querySelector("input[data-hm-ip]") as HTMLInputElement;
    expect(firstInput).toBeTruthy();
    fireEvent.change(firstInput, { target: { value: "192.168.1.1" } });
    // 首行被改写，其余 149 行不变（稳定 handlers + memo：编辑单行不污染他行）
    expect(captured[0].ip).toBe("192.168.1.1");
    expect(captured[1].ip).toBe("10.0.0.1");
    expect(captured.length).toBe(150);
  });
});
