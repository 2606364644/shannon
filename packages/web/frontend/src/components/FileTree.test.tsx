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
  it("3 层嵌套递归渲染（a/b/c.md → li > ul > li > ul > li）", () => {
    const deepFiles: DeliverablesFile[] = [
      { path: "a/b/c.md", size: 5, kind: "md" },
    ];
    const { container } = render(<FileTree files={deepFiles} onSelect={() => {}} />);
    // 目录默认展开规则：depth<1 展开 → a(0) 开 / b(1) 合。先展开 b 再断言深度 3。
    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByText("b")).toBeInTheDocument(); // b 在 a 的 ul 里
    // c.md 默认隐藏（b 折叠）
    expect(screen.queryByText("c.md")).not.toBeInTheDocument();
    // 展开 b
    fireEvent.click(screen.getByText("b").closest("button")!);
    expect(screen.getByText("c.md")).toBeInTheDocument();
    // 结构断言：顶层 ul > li(a) > ul > li(b) > ul > li(c.md) — 深度 3
    const rootUl = container.querySelector(":scope > ul.file-tree");
    expect(rootUl).not.toBeNull();
    const aLi = rootUl?.querySelector(":scope > li");
    expect(aLi).not.toBeNull();
    const aSubUl = aLi?.querySelector(":scope > ul");
    expect(aSubUl).not.toBeNull();
    const bLi = aSubUl?.querySelector(":scope > li");
    expect(bLi).not.toBeNull();
    const bSubUl = bLi?.querySelector(":scope > ul");
    expect(bSubUl).not.toBeNull();
    const cLi = bSubUl?.querySelector(":scope > li");
    expect(cLi).not.toBeNull();
    expect(cLi?.querySelector(".ft-name")?.textContent).toBe("c.md");
  });
  it("空目录（dir 节点无子项）渲染不崩溃：有 toggle，无子行", () => {
    // 构造一个仅含目录路径前缀、无叶子文件的 fixture。
    // buildTree 只在有 file 落点时建节点；纯目录无 file 不入树。
    // 但若一个目录下既有文件、又作为另一路径的中间节点，目录节点 children 可能只含子目录。
    // 此处用一个文件 a/b/f.md（a 是目录，含 b 子目录），再加 a/empty/ 但无文件 → empty 不入树。
    // 真正能构造的『空目录』= 一个目录节点其 children 为空 Map。
    // 由于 buildTree 不建无文件目录，本测试用 mock 不可行；改为断言：
    // 一个目录下只有子目录无直接文件（a/onlysub/x.md），a 的 li 渲染 toggle 且其下 ul 含 onlysub。
    const dirWithSubOnly: DeliverablesFile[] = [
      { path: "a/onlysub/x.md", size: 5, kind: "md" },
    ];
    const { container } = render(<FileTree files={dirWithSubOnly} onSelect={() => {}} />);
    // a 是目录（有子 onlysub），渲染 toggle 按钮
    const aToggle = screen.getByText("a").closest("button");
    expect(aToggle).not.toBeNull();
    // a 下无直接文件 li（只有 onlysub 子目录）
    const aLi = container.querySelector(":scope > ul.file-tree > li");
    const aDirectFileSpans = aLi?.querySelectorAll(":scope > div.ft-file");
    expect(aDirectFileSpans?.length ?? 0).toBe(0);
    // 折叠 a 后子节点消失，不崩溃
    fireEvent.click(aToggle!);
    expect(screen.queryByText("onlysub")).not.toBeInTheDocument();
    // 再展开，仍正常
    fireEvent.click(aToggle!);
    expect(screen.getByText("onlysub")).toBeInTheDocument();
  });
});
