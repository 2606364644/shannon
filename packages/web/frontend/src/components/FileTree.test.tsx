import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import i18n from "@/i18n";
import { FileTree } from "./FileTree";
import type { DeliverablesFile } from "../api/types";

const files: DeliverablesFile[] = [
  { path: "whitebox/comprehensive_report.md", size: 1000, kind: "md" },
  { path: "whitebox/ssrf_exploitation_queue.json", size: 100, kind: "exploitation_queue" },
  { path: "whitebox/attack_chains.json", size: 2, kind: "empty_json" },
];

describe("FileTree", () => {
  beforeEach(() => i18n.changeLanguage("zh"));
  afterEach(() => i18n.changeLanguage("zh"));
  it("渲染嵌套目录 + 文件（track 目录显示友好名）", () => {
    render(<FileTree files={files} onSelect={() => {}} />);
    expect(screen.getByText("白盒")).toBeInTheDocument();
    expect(screen.getByText("comprehensive_report.md")).toBeInTheDocument();
  });
  it("点击文件回调", () => {
    const onSelect = vi.fn();
    render(<FileTree files={files} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: /ssrf_exploitation_queue\.json/ }));
    expect(onSelect).toHaveBeenCalledWith(files[1]);
  });
  it("空 json 标记（结构断言：行内含『空』badge）", () => {
    const { container } = render(<FileTree files={files} onSelect={() => {}} />);
    // 文件行是 button；空 badge 与文件名同属一行
    const row = Array.from(container.querySelectorAll("button")).find((el) =>
      el.textContent?.includes("attack_chains.json"),
    );
    expect(row).toBeDefined();
    expect(row!.textContent).toContain("空");
  });
  it("嵌套结构：文件在目录 li 之下（结构断言）", () => {
    const { container } = render(<FileTree files={files} onSelect={() => {}} />);
    // 顶层 ul > li(whitebox) > ul > li(文件)
    const topLis = container.querySelectorAll(":scope > ul > li");
    expect(topLis.length).toBe(1);
    const wbLi = topLis[0];
    // whitebox 目录下应有 3 个文件 li
    const fileLis = wbLi.querySelectorAll("ul > li");
    expect(fileLis.length).toBe(3);
    // 文件名集合校验（结构）
    const names = Array.from(fileLis).map((li) => li.textContent ?? "");
    expect(names.some((n) => n.includes("comprehensive_report.md"))).toBe(true);
    expect(names.some((n) => n.includes("ssrf_exploitation_queue.json"))).toBe(true);
    expect(names.some((n) => n.includes("attack_chains.json"))).toBe(true);
  });
  it("目录展开/收起切换", () => {
    render(<FileTree files={files} onSelect={() => {}} />);
    const toggle = screen.getByRole("button", { name: /白盒/ });
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
    fireEvent.click(screen.getByRole("button", { name: /^b$/ }));
    expect(screen.getByText("c.md")).toBeInTheDocument();
    // 结构断言：顶层 ul > li(a) > ul > li(b) > ul > li(c.md) — 深度 3
    const rootUl = container.querySelector(":scope > ul");
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
    expect(cLi?.textContent).toContain("c.md");
  });
  it("空目录（dir 节点无子项）渲染不崩溃：有 toggle，无子行", () => {
    // 一个目录下只有子目录无直接文件（a/onlysub/x.md），a 的 li 渲染 toggle 且其下 ul 含 onlysub。
    const dirWithSubOnly: DeliverablesFile[] = [
      { path: "a/onlysub/x.md", size: 5, kind: "md" },
    ];
    const { container } = render(<FileTree files={dirWithSubOnly} onSelect={() => {}} />);
    // a 是目录（有子 onlysub），渲染 toggle 按钮
    const aToggle = screen.getByRole("button", { name: /^a$/ });
    expect(aToggle).not.toBeNull();
    // a 下无直接文件 button（只有 onlysub 子目录 button）
    const aLi = container.querySelector(":scope > ul > li");
    // a 的 div 行自身是 toggle，不应再有 ‘x.md’ 文件
    expect(aLi?.textContent).not.toContain("x.md");
    // 折叠 a 后子节点消失，不崩溃
    fireEvent.click(aToggle);
    expect(screen.queryByText("onlysub")).not.toBeInTheDocument();
    // 再展开，仍正常
    fireEvent.click(aToggle);
    expect(screen.getByText("onlysub")).toBeInTheDocument();
  });
  it("目录 toggle 有 aria-expanded，点击切换", () => {
    render(<FileTree files={[{ path: "d/f.json", size: 1, kind: "other_json" }]} onSelect={() => {}} />);
    // 用真实目录名 d 精确定位 toggle（文件行 button 的 accessible name 会包含 f.json）
    const dirToggle = screen.getByRole("button", { name: /^d$/ });
    // 初始 depth<1 → 展开
    expect(dirToggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(dirToggle);
    expect(dirToggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(dirToggle);
    expect(dirToggle).toHaveAttribute("aria-expanded", "true");
  });
  it("文件行是 button，点击触发 onSelect", () => {
    const onSelect = vi.fn();
    render(<FileTree files={[{ path: "f.json", size: 1, kind: "other_json" }]} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: /f\.json/ }));
    expect(onSelect).toHaveBeenCalledOnce();
  });

  it("big_json 标记『大』（结构断言：行内含『大』badge）", () => {
    const { container } = render(
      <FileTree files={[{ path: "whitebox/big.json", size: 999999, kind: "big_json" }]} onSelect={() => {}} />,
    );
    const row = Array.from(container.querySelectorAll("button")).find((el) =>
      el.textContent?.includes("big.json"),
    );
    expect(row).toBeDefined();
    expect(row!.textContent).toContain("大");
  });

  it("组合扫描三 track 目录各显示友好名（黑盒/融合），非 track 目录保留原名", () => {
    render(
      <FileTree
        files={[
          { path: "whitebox/a.md", size: 1, kind: "md" },
          { path: "blackbox/b.json", size: 1, kind: "other_json" },
          { path: "combined/c.md", size: 1, kind: "md" },
          { path: "agents/d.log", size: 1, kind: "other" },
        ]}
        onSelect={() => {}}
      />,
    );
    expect(screen.getByText("白盒")).toBeInTheDocument();
    expect(screen.getByText("黑盒")).toBeInTheDocument();
    expect(screen.getByText("融合")).toBeInTheDocument();
    // 非 track 目录（agents）不受友好名映射影响
    expect(screen.getByRole("button", { name: /^agents$/ })).toBeInTheDocument();
  });

  it("selectedPath 命中文件行高亮（aria-current），未命中行无", () => {
    render(
      <FileTree files={files} onSelect={() => {}} selectedPath="whitebox/comprehensive_report.md" />,
    );
    const selected = screen.getByRole("button", { name: /comprehensive_report\.md/ });
    expect(selected).toHaveAttribute("aria-current", "true");
    expect(selected.className).toContain("bg-accent");
    const other = screen.getByRole("button", { name: /ssrf_exploitation_queue\.json/ });
    expect(other).not.toHaveAttribute("aria-current");
  });

  describe("i18n", () => {
    afterEach(() => i18n.changeLanguage("zh"));

    it("切英文 empty/big 标记为 (empty)/(large)", () => {
      i18n.changeLanguage("en");
      const { container } = render(
        <FileTree
          files={[
            { path: "a/empty.json", size: 2, kind: "empty_json" },
            { path: "a/big.json", size: 999999, kind: "big_json" },
          ]}
          onSelect={() => {}}
        />,
      );
      // a 在 depth 0 默认展开，子文件直接可见
      expect(container.textContent).toContain("(empty)");
      expect(container.textContent).toContain("(large)");
      expect(container.textContent).not.toContain("（空）");
      expect(container.textContent).not.toContain("（大）");
    });
  });
});
