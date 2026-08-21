import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import i18n from "@/i18n";
import { COL_W, NODE_LABEL_Y1, PILL_HALF_H_MAX, ROW_H, PruningTreeFig } from "../PruningTreeFig";
import { BranchRow } from "../BranchRow";
import type { DataflowTree, DataflowBranch } from "@/api/types";

// 极小 fixture：一棵打通树（1 branch, track=gitnexus, verdict=vulnerable, 2 中间节点）
const vulnerableTree: DataflowTree = {
  tree_id: "T-VULN-01",
  vuln_class: "injection",
  sink: {
    label: "cursor.execute",
    file: "app/db.py",
    line: 42,
    rule_id: "py-sql-execute-raw",
    category: "sql",
    code: "cursor.execute(query)",
  },
  findings: [
    {
      id: "INJ-VULN-01",
      merge_source: "both",
      title: "SQL 注入",
      confidence: "high",
    },
  ],
  branches: [
    {
      branch_id: "F-01",
      track: "gitnexus",
      verdict: "vulnerable",
      verdict_reason: "一路无有效防护",
      source: { label: "req.query.name", type: "query", entry: "GET /api/users", file: "app/routes.ts", line: 10 },
      nodes: [
        {
          func: "UserController.list",
          file: "app/controllers/user.ts",
          line: 25,
          transformation: "concat",
          intermediate_vars: ["q"],
          code: "q = 'SELECT...' + name",
          has_code: true,
        },
        {
          func: "DBLayer.run",
          file: "app/db.ts",
          line: 38,
          transformation: null,
          intermediate_vars: [],
          code: "db.run(q)",
          has_code: true,
        },
      ],
      sanitizers: [],
    },
  ],
};

// 极小 fixture：一棵剪断树（verdict=safe，有 sanitizer.effective=true，残端不到 sink）
const safeTree: DataflowTree = {
  tree_id: "T-SAFE-01",
  vuln_class: "injection",
  sink: {
    label: "cursor.execute",
    file: "app/db.py",
    line: 42,
    rule_id: "py-sql-execute-raw",
    category: "sql",
    code: "cursor.execute(query)",
  },
  findings: [],
  branches: [
    {
      branch_id: "F-SAFE-01",
      track: "gitnexus",
      verdict: "safe",
      verdict_reason: "shlex.quote 覆盖拼接值",
      source: { label: "req.query.name", type: "query", entry: "GET /api/users", file: "app/routes.ts", line: 10 },
      nodes: [
        {
          func: "sanitize",
          file: "app/sanitize.ts",
          line: 30,
          transformation: "sanitize_hint:shlex.quote",
          intermediate_vars: ["safe_q"],
          code: "safe_q = shlex.quote(name)",
          has_code: true,
        },
      ],
      sanitizers: [{ name: "shlex.quote", defense_type: "shlex_quote", file: "app/sanitize.ts", line: 30, effective: true }],
    },
  ],
};

// safe-only 树（无 findings、无打通枝）→ sink 灰虚线圆环
const safeOnlyTree: DataflowTree = {
  ...safeTree,
  tree_id: "T-SAFE-ONLY-02",
};

describe("PruningTreeFig — SVG 剪枝树（spec §5 视觉语言）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));
  afterEach(() => i18n.changeLanguage("zh"));
  it("renders vulnerable branch as flowing red dashed path（打通枝）", () => {
    const { container } = render(<PruningTreeFig trees={[vulnerableTree]} />);
    const path = container.querySelector('path[data-branch="vulnerable"]');
    expect(path).toBeTruthy();
    // 流动动画 class（CSS @keyframes flow → stroke-dashoffset）
    expect(path?.getAttribute("class") ?? "").toContain("flow");
    expect(path?.getAttribute("class") ?? "").toContain("branch-vuln");
  });

  it("renders safe branch with ✂ marker + 残端不到 sink（剪断枝）", () => {
    const { container } = render(<PruningTreeFig trees={[safeTree]} />);
    const scissors = container.querySelector('[data-branch="safe"] [data-scissors]');
    expect(scissors).toBeTruthy();
    // 剪断枝绿实线 path class
    const safePath = container.querySelector('path[data-branch="safe"]');
    expect(safePath).toBeTruthy();
    expect(safePath?.getAttribute("class") ?? "").toContain("branch-safe");
    // 残端虚线 class（渐隐残端，不到 sink）
    const remnant = container.querySelector('path[data-branch="safe"][data-remnant]');
    expect(remnant).toBeTruthy();
  });

  it("aligns nodes to column x = step_index * COL_W（列对齐）", () => {
    const { container } = render(<PruningTreeFig trees={[vulnerableTree]} />);
    const node0 = container.querySelector('[data-node="0"]');
    expect(node0).toBeTruthy();
    expect(parseFloat(node0?.getAttribute("x") ?? "-1")).toBeCloseTo(0 * COL_W, 5);
    const node1 = container.querySelector('[data-node="1"]');
    expect(node1).toBeTruthy();
    expect(parseFloat(node1?.getAttribute("x") ?? "-1")).toBeCloseTo(1 * COL_W, 5);
  });

  it("sink 靶心：有打通枝 → 红实线圆环（脉动 class）；safe-only → 灰虚线圆环", () => {
    const { container: vulnC } = render(<PruningTreeFig trees={[vulnerableTree]} />);
    const vulnSink = vulnC.querySelector('[data-sink-target="vuln"]');
    expect(vulnSink).toBeTruthy();
    expect(vulnSink?.getAttribute("class") ?? "").toContain("sink-pulse");

    const { container: safeC } = render(<PruningTreeFig trees={[safeOnlyTree]} />);
    const safeSink = safeC.querySelector('[data-sink-target="safe"]');
    expect(safeSink).toBeTruthy();
    expect(safeSink?.getAttribute("class") ?? "").toContain("sink-idle");
  });

  it("source 青色 pill（--c-cyan）", () => {
    const { container } = render(<PruningTreeFig trees={[vulnerableTree]} />);
    const source = container.querySelector('[data-source]');
    expect(source).toBeTruthy();
    expect(source?.getAttribute("class") ?? "").toContain("source-pill");
  });

  it("折叠：剪断枝 >4 折叠为「+N 条枝被剪断」行（viewBox 动态调高）", () => {
    const safeBranch: DataflowBranch = { ...safeTree.branches[0], branch_id: "F-SAFE-EX" };
    const tree: DataflowTree = {
      ...safeTree,
      tree_id: "T-FOLD",
      branches: Array.from({ length: 6 }, (_, i) => ({ ...safeBranch, branch_id: `F-SAFE-${i}` })),
    };
    const { container } = render(<PruningTreeFig trees={[tree]} />);
    // 折叠提示存在
    const collapsed = container.querySelector('[data-collapsed-safe]');
    expect(collapsed).toBeTruthy();
    expect(collapsed?.textContent ?? "").toContain("+");
    expect(collapsed?.textContent ?? "").toContain("剪断");
  });

  it("verdict 取 finding/chain verdict（vulnerable/safe/unknown 三态）", () => {
    const unknownTree: DataflowTree = {
      ...vulnerableTree,
      tree_id: "T-UNK",
      findings: [],
      branches: [{ ...vulnerableTree.branches[0], verdict: "unknown", branch_id: "F-UNK" }],
    };
    const { container } = render(<PruningTreeFig trees={[unknownTree]} />);
    const unk = container.querySelector('path[data-branch="unknown"]');
    expect(unk).toBeTruthy();
    expect(unk?.getAttribute("class") ?? "").toContain("branch-unknown");
  });

  it("同名函数：青色点线弧 + 「同一函数」标注（不合并节点）", () => {
    // 两枝共享同名函数节点 → 同名函数弧
    const sharedNode = { ...vulnerableTree.branches[0].nodes[0] };
    const tree: DataflowTree = {
      ...vulnerableTree,
      tree_id: "T-SAMELINE",
      branches: [
        { ...vulnerableTree.branches[0], branch_id: "F-A", nodes: [sharedNode] },
        { ...vulnerableTree.branches[0], branch_id: "F-B", nodes: [{ ...sharedNode }] },
      ],
    };
    const { container } = render(<PruningTreeFig trees={[tree]} />);
    const arc = container.querySelector('[data-sameline]');
    expect(arc).toBeTruthy();
    expect(arc?.getAttribute("class") ?? "").toContain("sameline");
    // 每弧不带文字标签（多共享函数时 midX/midY 相近的弧标签互叠；语义进 LegendBar 图例）
    expect(container.querySelector("[data-sameline-label]")).toBeNull();
  });

  it("缩放平移：容器限高 520 + wheel 缩放 + 重置控件", () => {
    const { container } = render(<PruningTreeFig trees={[vulnerableTree]} />);
    const viewport = container.querySelector('[data-viewport]');
    expect(viewport).toBeTruthy();
    // 限高样式存在（max-height 520）
    expect(viewport?.getAttribute("data-max-height") ?? "").toContain("520");
    // 重置控件
    const reset = container.querySelector('[data-zoom-reset]');
    expect(reset).toBeTruthy();
  });

  // —— Fix round 1 守卫：spec §5 3 个硬要求 ——

  it("source pill 含 type + entry（spec §5 source 行：参数名 + type + METHOD /route）", () => {
    const { container } = render(<PruningTreeFig trees={[vulnerableTree]} />);
    const source = container.querySelector('[data-source]');
    expect(source).toBeTruthy();
    // source 元素内含副信息小字，含 type 与 entry
    const metaText = source?.querySelector('.source-meta-txt');
    expect(metaText?.textContent ?? "").toContain("query"); // type
    expect(metaText?.textContent ?? "").toContain("GET /api/users"); // entry
    // 主 pill 文本含 label
    const pillText = source?.querySelector('.source-pill-txt');
    expect(pillText?.textContent ?? "").toContain("req.query.name");
  });

  it("公共函数 ⟳ N 枝经过 节点下标（spec §5 独立行，与同一函数弧分开）", () => {
    // 两枝共享同名函数节点 → 公共函数下标 + 同一函数弧同时出现（两元素独立）
    const sharedNode = { ...vulnerableTree.branches[0].nodes[0] };
    const tree: DataflowTree = {
      ...vulnerableTree,
      tree_id: "T-PUBFUNC",
      branches: [
        { ...vulnerableTree.branches[0], branch_id: "F-A", nodes: [sharedNode] },
        { ...vulnerableTree.branches[0], branch_id: "F-B", nodes: [{ ...sharedNode }] },
      ],
    };
    const { container } = render(<PruningTreeFig trees={[tree]} />);
    // 公共函数下标存在（N=2 枝经过）
    const sub = container.querySelector('[data-pubfunc]');
    expect(sub).toBeTruthy();
    expect(sub?.textContent ?? "").toContain("公共函数");
    expect(sub?.textContent ?? "").toContain("2");
    // 同一函数点线弧也存在（两元素独立，不合并）
    const arc = container.querySelector('[data-sameline]');
    expect(arc).toBeTruthy();
    // 下标 tooltip 说明剪断哪几条枝（此处两枝均打通 → 无剪断枝 → tooltip 用 TooltipNone）
    const nodeWithSub = sub?.closest("g[data-node]");
    const nodeTitle = nodeWithSub?.querySelector("title");
    expect(nodeTitle?.textContent ?? "").toBeTruthy();
    expect(nodeTitle?.textContent ?? "").toContain("2");
  });

  it("跨树 source tooltip：同一入口流向多 sink 注「同一入口还流向：」", () => {
    // 两棵树共享同一 source（label+entry 相同）→ 第二棵树的 source tooltip 列出第一棵的 sink
    const tree2: DataflowTree = {
      ...vulnerableTree,
      tree_id: "T-CROSS-02",
      sink: { ...vulnerableTree.sink, label: "fs.writeFile" },
      branches: [
        {
          ...vulnerableTree.branches[0],
          branch_id: "F-CROSS",
          source: { label: "req.query.name", type: "query", entry: "GET /api/users", file: "r", line: 1 },
        },
      ],
    };
    const { container } = render(<PruningTreeFig trees={[vulnerableTree, tree2]} />);
    // 两棵树各一 source pill，至少 2 个
    const sources = container.querySelectorAll('[data-source]');
    expect(sources.length).toBeGreaterThanOrEqual(2);
    // 跨树 tooltip 集合：第一棵树(T1 cursor.execute) 的 source 应提示流向第二棵(fs.writeFile)；
    // 第二棵树(T2 fs.writeFile) 的 source 应提示流向第一棵(cursor.execute)。
    const tooltips = Array.from(sources).map((s) => s.querySelector("title")?.textContent ?? "");
    const crossTips = tooltips.filter((tt) => tt.includes("同一入口还流向"));
    expect(crossTips.length).toBeGreaterThanOrEqual(1);
    // 两棵树互相指向，tooltip 集合中应同时出现另一棵树的 sink 名
    const joined = crossTips.join(" | ");
    expect(joined).toContain("cursor.execute");
    expect(joined).toContain("fs.writeFile");
  });

  // —— Fix round 1 Minor①/②：白话文案补齐（无输入到达 / 存储中转）——

  it("safe-only 灰靶心带「无输入到达」白话（spec §5 表：禁「未被触及」）", () => {
    const { container } = render(<PruningTreeFig trees={[safeOnlyTree]} />);
    const g = container.querySelector('[data-sink-target="safe"]')!;
    // 直显小字 + 原生 <title> 两处同一白话
    expect(g.querySelector("[data-sink-noinput]")?.textContent).toBe("无输入到达");
    expect(g.querySelector("title")?.textContent ?? "").toContain("无输入到达");
    // 打通树靶心不带该标注
    const { container: vulnC } = render(<PruningTreeFig trees={[vulnerableTree]} />);
    expect(vulnC.querySelector('[data-sink-target="vuln"] [data-sink-noinput]')).toBeNull();
  });

  it("2ND 存储中转枝：source pill 琥珀「⟳ 存储中转」标记 + tooltip 白话", () => {
    const storageTree: DataflowTree = {
      ...vulnerableTree,
      tree_id: "T-2ND",
      branches: [
        {
          ...vulnerableTree.branches[0],
          branch_id: "F-2ND",
          source: { label: "db.users.bio", type: "storage", entry: null, file: "db.ts", line: 7 },
        },
      ],
    };
    const { container } = render(<PruningTreeFig trees={[storageTree]} />);
    const mark = container.querySelector("[data-storage-relay]");
    expect(mark?.textContent ?? "").toContain("存储中转");
    // tooltip 含 spec §5 白话全句
    const tip = container.querySelector("[data-source] title")?.textContent ?? "";
    expect(tip).toContain("先存进数据库");
    expect(tip).toContain("读出来才发起请求");
    // 工程词 storage 不再直译进 pill 副行
    const meta = container.querySelector(".source-meta-txt");
    expect(meta?.textContent ?? "").not.toContain("storage");
  });
});

describe("BranchRow — 枝条明细 + 代码展开", () => {
  beforeEach(() => i18n.changeLanguage("zh"));
  afterEach(() => i18n.changeLanguage("zh"));
  it("链级标签：vulnerable → 打通 · 一路无有效防护", () => {
    const { container } = render(<BranchRow branch={vulnerableTree.branches[0]} />);
    expect(container.textContent ?? "").toContain("打通");
    expect(container.textContent ?? "").toContain("一路无有效防护");
  });

  it("链级标签：safe → 剪断 · 在 X 被拦下（X = 剪断点函数名）", () => {
    const { container } = render(<BranchRow branch={safeTree.branches[0]} />);
    expect(container.textContent ?? "").toContain("剪断");
    expect(container.textContent ?? "").toContain("被拦下");
    // 剪断点函数名进标签
    expect(container.textContent ?? "").toContain("sanitize");
  });

  it("节点点击展开 code（has_code:true 节点）", () => {
    const { container } = render(<BranchRow branch={vulnerableTree.branches[0]} />);
    // 节点行可点击展开
    const nodeBtn = container.querySelector('[data-node-toggle]');
    expect(nodeBtn).toBeTruthy();
    // 初始折叠：code 区不可见
    expect(container.querySelector('[data-node-code]')).toBeNull();
    // 点击展开
    fireEvent.click(nodeBtn!);
    const code = container.querySelector('[data-node-code]');
    expect(code).toBeTruthy();
    expect(code?.textContent ?? "").toContain("SELECT");
  });

  it("has_code:false 降级「LLM 扫描的节点不带源码，agent 原话」", () => {
    const noCodeBranch: DataflowBranch = {
      ...vulnerableTree.branches[0],
      nodes: [{ ...vulnerableTree.branches[0].nodes[0], code: null, has_code: false }],
    };
    const { container } = render(<BranchRow branch={noCodeBranch} />);
    const nodeBtn = container.querySelector('[data-node-toggle]');
    expect(nodeBtn).toBeTruthy();
    fireEvent.click(nodeBtn!);
    const code = container.querySelector('[data-node-code]');
    expect(code?.textContent ?? "").toContain("LLM 扫描的节点不带源码");
  });

  it("BranchRow 带 data-branch-id 锚点属性（图↔行联动的挂点，联动本体见 PruningTreeFig 联动用例）", () => {
    const { container } = render(<BranchRow branch={vulnerableTree.branches[0]} />);
    const row = container.querySelector('[data-branch-row]');
    expect(row).toBeTruthy();
    expect(row?.getAttribute("data-branch-id") ?? "").toBe(vulnerableTree.branches[0].branch_id);
  });

  it("2ND 存储中转枝：枝条标签渲染「⟳ 存储中转」+ title 白话全句", () => {
    const storageBranch: DataflowBranch = {
      ...vulnerableTree.branches[0],
      source: { label: "db.users.bio", type: "storage", entry: null, file: "db.ts", line: 7 },
    };
    const { container } = render(<BranchRow branch={storageBranch} />);
    const mark = container.querySelector("[data-storage-relay]");
    expect(mark?.textContent ?? "").toContain("存储中转");
    expect(mark?.getAttribute("title") ?? "").toContain("先存进数据库");
    expect(mark?.getAttribute("title") ?? "").toContain("读出来才发起请求");
    // 非存储枝不带标记
    const { container: plain } = render(<BranchRow branch={vulnerableTree.branches[0]} />);
    expect(plain.querySelector("[data-storage-relay]")).toBeNull();
  });

  it("超长 verdict_reason 限两行（line-clamp-2）+ title 全文（真实数据 300 字判定理由不再挤爆首行）", () => {
    const LONG_REASON =
      "数据被嵌入 HTML 注释上下文但仅应用 JSON 编码，外部可控的 cn_name 含 --> 时提前闭合注释，" +
      "其后的 mention 与注入标记将作为评论正文被渲染；这是编码与最终渲染上下文不匹配的典型场景，".repeat(3);
    const branch: DataflowBranch = { ...vulnerableTree.branches[0], verdict_reason: LONG_REASON };
    const { container } = render(<BranchRow branch={branch} />);
    const reason = container.querySelector("[data-branch-verdict-reason]");
    expect(reason).toBeTruthy();
    expect(reason?.className).toContain("line-clamp-2");
    expect(reason?.getAttribute("title") ?? "").toBe(LONG_REASON);
  });
});

// —— 图↔行 hover 双向联动 + 点枝条展开（spec §5「交互」段；final fix F1）——
// TreeCard 是 SVG 枝条与 BranchRow 的共同父级，联动 state 在其内——
// 以下用例在 PruningTreeFig 整树下验证真实联动（非属性存在性断言）。

// 两枝打通树（同节点结构，branch_id 区分），供联动单侧断言
const twoBranchTree: DataflowTree = {
  ...vulnerableTree,
  tree_id: "T-LINK",
  branches: [
    { ...vulnerableTree.branches[0], branch_id: "F-A" },
    { ...vulnerableTree.branches[0], branch_id: "F-B" },
  ],
};

describe("图↔行 hover 双向联动 + 点枝条展开（spec §5 交互）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));
  afterEach(() => i18n.changeLanguage("zh"));

  it("hover SVG 枝条 → path 加高亮 class + 对应 BranchRow 高亮（图→行；另一枝不受影响）", () => {
    const { container } = render(<PruningTreeFig trees={[twoBranchTree]} />);
    const gA = container.querySelector('g[data-branch-id="F-A"]');
    expect(gA).toBeTruthy();
    fireEvent.mouseEnter(gA!);
    // SVG 侧：g data-hovered + 主 path hovered class
    expect(gA?.getAttribute("data-hovered")).toBe("");
    const pathA = container.querySelector('g[data-branch-id="F-A"] path[data-branch]');
    expect(pathA?.getAttribute("class") ?? "").toContain("hovered");
    // 行侧：仅 F-A 明细行高亮
    const rowA = container.querySelector('[data-branch-row][data-branch-id="F-A"]');
    expect(rowA?.getAttribute("data-hovered")).toBe("");
    expect(rowA?.className).toContain("branch-row-hovered");
    const rowB = container.querySelector('[data-branch-row][data-branch-id="F-B"]');
    expect(rowB?.hasAttribute("data-hovered")).toBe(false);
    // 离开 → 双侧复原
    fireEvent.mouseLeave(gA!);
    expect(container.querySelector("[data-branch-row][data-hovered]")).toBeNull();
    expect(pathA?.getAttribute("class") ?? "").not.toContain("hovered");
  });

  it("hover BranchRow → 对应 SVG 枝条高亮（行→图，双向）", () => {
    const { container } = render(<PruningTreeFig trees={[twoBranchTree]} />);
    const rowB = container.querySelector('[data-branch-row][data-branch-id="F-B"]');
    expect(rowB).toBeTruthy();
    fireEvent.mouseEnter(rowB!);
    const gB = container.querySelector('g[data-branch-id="F-B"]');
    expect(gB?.getAttribute("data-hovered")).toBe("");
    const pathB = container.querySelector('g[data-branch-id="F-B"] path[data-branch]');
    expect(pathB?.getAttribute("class") ?? "").toContain("hovered");
    // F-A 不受影响
    const gA = container.querySelector('g[data-branch-id="F-A"]');
    expect(gA?.hasAttribute("data-hovered")).toBe(false);
    fireEvent.mouseLeave(rowB!);
    expect(pathB?.getAttribute("class") ?? "").not.toContain("hovered");
  });

  it("点枝条 → 选中对应 BranchRow：行高亮 + 展开首个节点 code；再点取消", () => {
    const { container } = render(<PruningTreeFig trees={[vulnerableTree]} />);
    const g = container.querySelector('g[data-branch-id="F-01"]');
    expect(g).toBeTruthy();
    // 初始：无选中、code 收起
    expect(container.querySelector("[data-branch-row][data-selected]")).toBeNull();
    expect(container.querySelector("[data-node-code]")).toBeNull();
    fireEvent.click(g!);
    // 行侧：选中高亮 + 展开首个节点明细（code 含 SELECT 拼接）
    const row = container.querySelector('[data-branch-row][data-branch-id="F-01"]');
    expect(row?.getAttribute("data-selected")).toBe("");
    expect(row?.className).toContain("branch-row-selected");
    const code = row?.querySelector("[data-node-code]");
    expect(code?.textContent ?? "").toContain("SELECT");
    // SVG 侧：path selected class
    const path = container.querySelector('path[data-branch="vulnerable"]');
    expect(path?.getAttribute("class") ?? "").toContain("selected");
    // 再点同一枝 → 取消选中（已展开的 code 不强制收起，交还行内节点按钮控制）
    fireEvent.click(g!);
    expect(container.querySelector("[data-branch-row][data-selected]")).toBeNull();
    expect(path?.getAttribute("class") ?? "").not.toContain("selected");
  });

  it("节点带 transform（局部坐标系平移到 (x,y)）", () => {
    const { container } = render(<PruningTreeFig trees={[vulnerableTree]} />);
    const node1 = container.querySelector('[data-node="1"]');
    expect(node1?.getAttribute("transform") ?? "").toContain("translate");
    const source = container.querySelector("[data-source]");
    expect(source?.getAttribute("transform") ?? "").toContain("translate");
  });

  it("SVG 内不用 data-tooltip CSS 浮层（::after 在 SVG 无 containing block 定位不可靠），tooltip 走原生 <title>", () => {
    // vitest 默认 stub CSS 导入（?raw 也拿不到原文），用 node:fs 直读源文件；
    // 路径锚定本测试文件（expect.getState().testPath），不依赖进程 cwd。
    const testDir = dirname(expect.getState().testPath ?? "");
    const tokensCss = readFileSync(resolve(testDir, "../../../styles/tokens.css"), "utf-8");
    // 2026-08-21 重叠修复：SVG 侧 data-tooltip 浮层规则已删（::after 定位不可靠 → 全部 <title>）
    expect(tokensCss).not.toMatch(/\[data-tooltip\]:hover::after/);
    // 联动高亮 class 消费方仍在（hovered/selected → 枝条加粗提亮）
    expect(tokensCss).toMatch(/\.branch-(vuln|safe|unknown)\.(hovered|selected)/);
    expect(tokensCss).toContain(".branch-row-hovered");
    expect(tokensCss).toContain(".branch-row-selected");
  });
});

// ===== 真实数据布局回归（2026-08-21 报告：其他环境真实数据下图错乱）=====
// 旧 fixture 全是短标签 + 剪断点恰在末节点，覆盖不到真实形态：
// - LLM dataflow_steps 的 label 常为长串（含路径/中文描述），列宽 180 固定 → 文字互相重叠；
// - GitNexus safe 枝防护在中途节点 → 主 path 画到枝尾再折回剪断点 + 剪断点后节点照画 → 连线错乱；
// - 深链树（多列）svg width=100% 等比压缩进容器 → 字号缩到不可读（文字「缺失」）。
describe("PruningTreeFig — 真实数据布局回归（长标签/深链/中途剪断）", () => {
  // 中途剪断：4 节点，effective sanitizer 在第 2 节点（line=30）→ cutStep=2
  const midCutTree: DataflowTree = {
    tree_id: "T-MIDCUT-01",
    vuln_class: "injection",
    sink: {
      label: "cursor.execute", file: "app/db.py", line: 42,
      rule_id: "py-sql-execute-raw", category: "sql", code: "cursor.execute(query)",
    },
    findings: [],
    branches: [
      {
        branch_id: "F-MIDCUT",
        track: "gitnexus",
        verdict: "safe",
        verdict_reason: "shlex.quote 覆盖拼接值",
        source: { label: "req.query.name", type: "query", entry: "GET /api/users", file: "app/routes.ts", line: 10 },
        nodes: [
          { func: "UserController.list", file: "app/controllers/user.ts", line: 25, transformation: null, intermediate_vars: [], code: null, has_code: false },
          { func: "sanitize", file: "app/sanitize.ts", line: 30, transformation: "sanitize_hint:shlex.quote", intermediate_vars: [], code: null, has_code: false },
          { func: "DBLayer.run", file: "app/db.ts", line: 50, transformation: null, intermediate_vars: [], code: null, has_code: false },
          { func: "cursor.execute", file: "app/db.py", line: 42, transformation: null, intermediate_vars: [], code: null, has_code: false },
        ],
        sanitizers: [{ name: "shlex.quote", defense_type: "shlex_quote", file: "app/sanitize.ts", line: 30, effective: true }],
      },
    ],
  };

  // 长标签：LLM 轨真实形态（自然语言 label + 长 entry + 长 sink 名）
  const LONG_FUNC = "handleProfileUpdate 接收 req.body 并拼接 MongoDB 更新文档";
  const LONG_SOURCE = "req.body.profile.displayName（用户资料昵称字段）";
  const LONG_ENTRY = "POST /api/profile/:id/update-settings";
  const LONG_SINK = "MongoClient.db.collection.insertOne";
  const longLabelTree: DataflowTree = {
    ...vulnerableTree,
    tree_id: "T-LONG-01",
    sink: { ...vulnerableTree.sink, label: LONG_SINK },
    branches: [
      {
        ...vulnerableTree.branches[0],
        branch_id: "F-LONG",
        source: { label: LONG_SOURCE, type: "body", entry: LONG_ENTRY, file: "app/routes.ts", line: 10 },
        nodes: [
          { ...vulnerableTree.branches[0].nodes[0], func: LONG_FUNC },
          { ...vulnerableTree.branches[0].nodes[1], func: "db.collection.insertOne" },
        ],
      },
    ],
  };

  it("中途剪断枝：主 path 不折返（x 单调），剪断点之后节点不渲染", () => {
    const { container } = render(<PruningTreeFig trees={[midCutTree]} />);
    // 剪断点（step 2）之后的节点（step 3/4）不渲染——spec §5：绿实线至防护节点 + 残端，不到 sink
    const nodeSteps = [...container.querySelectorAll('[data-branch="safe"] [data-node]')].map((el) =>
      el.getAttribute("data-node"),
    );
    expect(nodeSteps).toEqual(["0", "1", "2"]);
    // 主 path 的 x 坐标不出现剪断点之后的列（COL_W=180：xOf(3)=540、xOf(4)=720）——无折返
    const d = container.querySelector('path[data-branch="safe"]')?.getAttribute("d") ?? "";
    expect(d).not.toContain("540");
    expect(d).not.toContain("720");
  });

  it("节点长函数名两行拆分（≤2 行列内预算，装得下则全文显示），超长时 <title> 保留全名", () => {
    const { container } = render(<PruningTreeFig trees={[longLabelTree]} />);
    const label = container.querySelector('[data-node="1"] [data-node-label]');
    expect(label).toBeTruthy();
    // 2026-08-21 两行化：两行合计显示信息远多于旧单行截断（~26 半角），仅尾部留 …
    const tspans = label?.querySelectorAll("tspan") ?? [];
    expect(tspans.length).toBe(2);
    const shown = label?.textContent ?? "";
    expect(shown).toContain("handleProfileUpdate 接收 req.body");
    expect(shown.replace("…", "").length).toBeGreaterThan(40);
    // 更长的 func（两行也装不下）→ 第二行 …，全名进 <title>
    const HUGE_FUNC = "list_work_item_push_records extract_text: 从 CSV/需求描述逐字段抽取产品经理与测试负责人等 6 类角色字段值并拼接过滤条件";
    const hugeFuncTree: DataflowTree = {
      ...vulnerableTree,
      tree_id: "T-HUGEFUNC",
      branches: [
        {
          ...vulnerableTree.branches[0],
          branch_id: "F-HUGEFUNC",
          nodes: [
            { ...vulnerableTree.branches[0].nodes[0], func: HUGE_FUNC },
            { ...vulnerableTree.branches[0].nodes[1], func: "db.run" },
          ],
        },
      ],
    };
    const { container: c2 } = render(<PruningTreeFig trees={[hugeFuncTree]} />);
    const shown2 = c2.querySelector('[data-node="1"] [data-node-label]')?.textContent ?? "";
    expect(shown2).toContain("…");
    expect(shown2.replace("…", "").length).toBeLessThan(HUGE_FUNC.length);
    expect(c2.querySelector('[data-node="1"] title')?.textContent ?? "").toContain(HUGE_FUNC);
  });

  it("source 长 label / 长 entry 截断；sink 长 label 截断且全名可见", () => {
    const { container } = render(<PruningTreeFig trees={[longLabelTree]} />);
    const srcLabel = container.querySelector("[data-source] [data-source-label]");
    expect(srcLabel).toBeTruthy();
    expect((srcLabel?.textContent ?? "").length).toBeLessThan(LONG_SOURCE.length);
    expect(srcLabel?.textContent ?? "").toContain("…");
    // 副行（type · entry）同样受列宽约束
    const srcMeta = container.querySelector("[data-source] [data-source-meta]");
    expect((srcMeta?.textContent ?? "").length).toBeLessThanOrEqual(LONG_ENTRY.length);
    // sink label 两行拆分（2026-08-21：不再单行一刀切——此长度两行可全文显示，不出右边界）
    const sinkLabel = container.querySelector("[data-sink-target] [data-sink-label]");
    expect(sinkLabel).toBeTruthy();
    const sinkTspans = sinkLabel?.querySelectorAll("tspan") ?? [];
    expect(sinkTspans.length).toBeLessThanOrEqual(2);
    expect(sinkLabel?.textContent ?? "").toBe(LONG_SINK);
    const sinkG = container.querySelector("[data-sink-target]");
    expect(sinkG?.querySelector("title")?.textContent ?? "").toContain(LONG_SINK);
  });

  it("svg 按像素宽渲染（宽树不整图压缩致文字不可读），viewBox 覆盖 pill 左缘与 sink 右侧", () => {
    const { container } = render(<PruningTreeFig trees={[midCutTree]} />);
    const svg = container.querySelector("svg");
    // width 是像素数（非 100%）：深链树不再被等比压进容器、字号不再缩到不可读
    const w = svg?.getAttribute("width") ?? "";
    expect(w).not.toBe("100%");
    expect(w).toMatch(/^\d+(\.\d+)?$/);
    // viewBox：minX < 0（容纳 source pill 左缘 -6 与盾外圈）；宽度 ≥ sink x + 0.9 列（容纳 sink 靶心+label）
    const vb = (svg?.getAttribute("viewBox") ?? "").split(/\s+/).map(Number);
    expect(vb.length).toBe(4);
    expect(vb[0]).toBeLessThan(0);
    const sinkX = 5 * COL_W; // midCutTree sinkCol = 4+1
    expect(vb[0] + vb[2]).toBeGreaterThanOrEqual(sinkX + COL_W * 0.9);
  });
});

// ===== 真实数据重叠修复（2026-08-21 第二轮：requirement-sec-review-20260821-044018）=====
// 真实 LLM 轨数据（每树 1 枝、自然语言长 label/entry/func）暴露的布局重叠：
// - source 副行（entry）裸画在 pill 底边外 y+15，与 step-1 节点标签（基线 y+25、
//   横向左缘可至 x+102）横向重叠 ~58px 且纵向同带 → 文字互叠；
// - SVG <g> 上 data-tooltip 的 CSS ::after position:absolute 无 containing block
//   （SVG 元素不支持 CSS position）→ 浮层定位回退到视口容器，所有 hover tooltip 叠到同一处；
// - ZoomViewport wheel 一律 preventDefault 缩放（页面滚不动）+ 拖拽 translate 与
//   overflow:auto 滚动条双轨错位（图被 translate 移出滚动条可达范围）。
describe("PruningTreeFig — 真实数据重叠修复（pill 副行/标签带/tooltip/缩放交互）", () => {
  const HUGE_ENTRY =
    "CLI push {url} [无中间件,本地 CLI 触发]; CLI push(批量) [无中间件]; daemon push-worker (src/daemon/worker.py:165-211) [无中间件,常驻线程每 600s 触发]";
  const hugeTree: DataflowTree = {
    ...vulnerableTree,
    tree_id: "T-HUGE-01",
    sink: { ...vulnerableTree.sink, label: "add_workitem_comment 'content': comment_content → MCP add_comment" },
    branches: [
      {
        ...vulnerableTree.branches[0],
        branch_id: "F-HUGE",
        verdict_reason:
          "数据被嵌入 HTML 注释上下文但仅应用 JSON 编码，外部可控的 cn_name 含 --> 时提前闭合注释，其后的 mention 与注入标记将作为评论正文被渲染；这是编码与最终渲染上下文不匹配的典型场景。",
        source: {
          label: "writer.list_work_item_push_records: extract_text(F.PRODUCT_MANAGER)",
          type: null,
          entry: HUGE_ENTRY,
          file: "src/feishu/writer.py",
          line: 172,
        },
        nodes: [
          {
            func: "pusher.run_batch: product_manager = record['product_manager']",
            file: "src/commands/pusher.py",
            line: 178,
            transformation: null,
            intermediate_vars: [],
            code: null,
            has_code: false,
          },
          {
            func: "_resolve_mention: cn_name = name_cn or cn_name or nick (回退攻击者值)",
            file: "src/oa/client.py",
            line: 82,
            transformation: null,
            intermediate_vars: [],
            code: null,
            has_code: false,
          },
        ],
      },
    ],
  };

  it("source 副行收进 pill：rect 高度按副行撑起，副行基线落在 rect 内（不再裸画 pill 外与节点标签带重叠）", () => {
    const { container } = render(<PruningTreeFig trees={[hugeTree]} />);
    const rect = container.querySelector('[data-source] rect.source-pill');
    const meta = container.querySelector('[data-source] [data-source-meta]');
    expect(rect).toBeTruthy();
    expect(meta).toBeTruthy();
    const h = parseFloat(rect!.getAttribute("height") ?? "0");
    const y = parseFloat(rect!.getAttribute("y") ?? "0");
    const metaY = parseFloat(meta!.getAttribute("y") ?? "999");
    // 有副行 → pill 高度撑起（≥34），副行基线在 rect 底边之内
    expect(h).toBeGreaterThanOrEqual(34);
    expect(metaY).toBeLessThanOrEqual(y + h);
  });

  it("节点标签两行渲染：tspan ≤ 2 且两行合计显示信息多于单行截断（自然语言 func 不再一刀切到 25 字符）", () => {
    const { container } = render(<PruningTreeFig trees={[hugeTree]} />);
    const label = container.querySelector('[data-node="1"] [data-node-label]');
    expect(label).toBeTruthy();
    const tspans = label?.querySelectorAll("tspan") ?? [];
    expect(tspans.length).toBeLessThanOrEqual(2);
    expect(tspans.length).toBeGreaterThanOrEqual(2); // 此 fixture func 必然拆两行
    const shown = Array.from(tspans).map((t) => t.textContent ?? "").join("");
    // 两行合计显示的字符数 > 单行预算（~26 半角）——长 func 信息量显著提升
    expect(shown.replace("…", "").length).toBeGreaterThan(26);
  });

  it("布局几何常量锁定：pill 底边与节点标签顶带纵向错开 ≥ 3px（横向重叠区不再同带）", () => {
    // PILL_HALF_H_MAX = 有副行 pill 半高；NODE_LABEL_Y1 = 标签首行基线；FONT_ASCENT ≈ 8（10px 字）
    expect(PILL_HALF_H_MAX).toBeLessThanOrEqual(NODE_LABEL_Y1 - 8 - 3);
    // 行高足够容纳：pill（≤半高）+ 标签两行 + 公共函数下标 + 下一行 pill 顶
    expect(ROW_H).toBeGreaterThanOrEqual(NODE_LABEL_Y1 + 11 + 11 + PILL_HALF_H_MAX + 4);
  });

  it("SVG 内 tooltip 全部用原生 <title>（data-tooltip 的 CSS 浮层在 SVG 内无 containing block，定位不可靠）", () => {
    const { container } = render(<PruningTreeFig trees={[hugeTree]} />);
    expect(container.querySelectorAll("svg [data-tooltip]").length).toBe(0);
    // 节点全名进 <title>
    const nodeTitle = container.querySelector('[data-node="1"] title');
    expect(nodeTitle?.textContent ?? "").toContain("pusher.run_batch");
    // source 副行超长 entry → 全文进 <title>
    const srcTitle = container.querySelector("[data-source] title");
    expect(srcTitle?.textContent ?? "").toContain(HUGE_ENTRY);
  });

  it("wheel 无修饰键放行页面滚动（不 preventDefault），Ctrl+wheel 才缩放（svg width 随 scale 联动）", () => {
    const { container } = render(<PruningTreeFig trees={[hugeTree]} />);
    const viewport = container.querySelector('[data-viewport]') as HTMLElement;
    expect(viewport).toBeTruthy();
    const svg = container.querySelector("svg");
    const baseW = parseFloat(svg?.getAttribute("width") ?? "0");
    // 无修饰 wheel：不取消默认行为（页面滚动继续）
    const plain = fireEvent.wheel(viewport, { deltaY: 120 });
    expect(plain).toBe(true);
    expect(parseFloat(svg?.getAttribute("width") ?? "0")).toBeCloseTo(baseW, 5);
    // Ctrl+wheel：接管默认（浏览器页缩放）→ 图缩放（width × 1.1）
    const zoomed = fireEvent.wheel(viewport, { deltaY: -120, ctrlKey: true });
    expect(zoomed).toBe(false);
    expect(parseFloat(svg?.getAttribute("width") ?? "0")).toBeCloseTo(baseW * 1.1, 1);
  });

  it("拖拽平移驱动滚动条（scrollLeft/scrollTop），无 CSS translate 双轨错位", () => {
    const { container } = render(<PruningTreeFig trees={[hugeTree]} />);
    const viewport = container.querySelector('[data-viewport]') as HTMLElement;
    expect(viewport).toBeTruthy();
    fireEvent.mouseDown(viewport, { clientX: 300, clientY: 200 });
    fireEvent.mouseMove(viewport, { clientX: 240, clientY: 170 });
    // 向左拖 60px → 内容右移 → scrollLeft +60（程序化滚动，滚动条同步）
    expect(viewport.scrollLeft).toBeCloseTo(60, 0);
    expect(viewport.scrollTop).toBeCloseTo(30, 0);
    fireEvent.mouseUp(viewport);
    // 无 translate transform（旧实现 translate(tx,ty) 与滚动条双轨 → 图被移出可达范围）
    const inner = viewport.firstElementChild as HTMLElement;
    expect(inner.style.transform).toBe("");
  });
});
