import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { FileTree } from "./FileTree";
import type { DeliverablesFile } from "../api/types";

const files: DeliverablesFile[] = [
  { path: "whitebox/comprehensive_report.md", size: 1000, kind: "md" },
  { path: "whitebox/ssrf_exploitation_queue.json", size: 100, kind: "exploitation_queue" },
  { path: "whitebox/attack_chains.json", size: 2, kind: "empty_json" },
];

describe("FileTree", () => {
  it("渲染嵌套目录 + 文件", () => {
    render(<FileTree files={files} onSelect={() => {}} />);
    expect(screen.getByText("whitebox")).toBeInTheDocument();
    expect(screen.getByText("comprehensive_report.md")).toBeInTheDocument();
  });
  it("点击文件回调", () => {
    const onSelect = vi.fn();
    render(<FileTree files={files} onSelect={onSelect} />);
    fireEvent.click(screen.getByText("ssrf_exploitation_queue.json"));
    expect(onSelect).toHaveBeenCalledWith(files[1]);
  });
  it("空 json 标记（结构断言：行内含 trace badge）", () => {
    const { container } = render(<FileTree files={files} onSelect={() => {}} />);
    // 文件名 span + 空 badge 同属一行 .ft-file
    const row = Array.from(container.querySelectorAll(".ft-file")).find((el) =>
      el.querySelector(".ft-name")?.textContent === "attack_chains.json",
    );
    expect(row).toBeDefined();
    expect(row!.textContent).toContain("空");
  });
  it("嵌套结构：文件在目录 li 之下（结构断言）", () => {
    const { container } = render(<FileTree files={files} onSelect={() => {}} />);
    // 顶层 ul > li(whitebox) > ul > li(文件)
    const topLis = container.querySelectorAll(":scope > ul.file-tree > li");
    expect(topLis.length).toBe(1);
    const wbLi = topLis[0];
    // whitebox 目录下应有 3 个文件 li
    const fileLis = wbLi.querySelectorAll("ul > li");
    expect(fileLis.length).toBe(3);
    // 文件名集合校验（结构）
    const names = Array.from(fileLis).map((li) => li.querySelector(".ft-name")?.textContent ?? "");
    expect(names.some((n) => n.includes("comprehensive_report.md"))).toBe(true);
    expect(names.some((n) => n.includes("ssrf_exploitation_queue.json"))).toBe(true);
    expect(names.some((n) => n.includes("attack_chains.json"))).toBe(true);
  });
  it("目录展开/收起切换", () => {
    render(<FileTree files={files} onSelect={() => {}} />);
    const toggle = screen.getByText(/whitebox/).closest("button")!;
    // 默认 depth<1 展开，子文件可见
    expect(screen.getByText("comprehensive_report.md")).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.queryByText("comprehensive_report.md")).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.getByText("comprehensive_report.md")).toBeInTheDocument();
  });
});
