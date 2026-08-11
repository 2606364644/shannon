/**
 * 报告章节级切分：把「攻击链」二级章节从报告 md 中独立切出。
 *
 * 架构语义（见 spec 2026-07-14-report-attack-chain-section-design §2）：
 * - 单点漏洞（vuln agent / GitNexus 轨产，ID 形如 PREFIX-VULN-NN / PREFIX-GN-NN）→ 单漏洞卡片网格
 * - 攻击链（仅 attack-chain agent 产，ID 形如 llm-chain-N）→ 独立攻击链 section，**不进**单漏洞网格
 *
 * 本函数只做「分割 + 计数」，**不**把 llm-chain-N 解析为 vuln block、**不**经 parseVulnBlock、
 * **不**进 vuln segment。VULN_HEADING_RE 保持只认单点 vuln ID。
 */

export interface AttackChainSplit {
  /** 攻击链章节之前的 md（执行摘要、单漏洞章节等） */
  before: string;
  /** 攻击链章节标题行**之后**的内容（不含 `## 攻击链` 标题行本身——标题由组件渲染，避免重复） */
  sectionMd: string;
  /** 攻击链章节之后的 md（通常为空，攻击链章节一般在文末） */
  after: string;
  /** 章节内 `### llm-chain-N` 标题数量 */
  count: number;
}

/** 攻击链条目标题：`### llm-chain-<数字>`（仅用于计数，不解析为 vuln）。 */
const LLM_CHAIN_HEADING_RE = /^### llm-chain-\d+\b/;

/**
 * 判断一行是否是「攻击链」二级标题。
 * 命中条件：`^## ` 开头，且标题文本（转小写、截到首个括号/冒号、去标点后）
 * 包含「攻击链」或「attackchain」。容错中英文 / 有无括号后缀等措辞变体。
 *
 * 脆弱点（显式记录）：此识别依赖报告生成层（report agent）的章节标题措辞；
 * 若生成层改措辞，需同步本规则。
 */
function isAttackChainHeading(line: string): boolean {
  const m = /^##\s+(.+)$/.exec(line);
  if (!m) return false;
  const text = m[1]
    .toLowerCase()
    .replace(/[（(:：].*$/, "") // 截到首个全角/半角括号或冒号
    .replace(/[^a-z一-龥]/g, ""); // 去标点空格，只留字母与中文
  return text.includes("攻击链") || text.includes("attackchain");
}

/**
 * 把报告 md 切成 [before, 攻击链章节, after] 三段。
 * 无攻击链章节时返回 null（整段 md 视作单点漏洞 md，attackChainCount=0，老报告兼容）。
 */
export function splitAttackChainSection(md: string): AttackChainSplit | null {
  const lines = md.split(/\r?\n/);

  let startIdx = -1;
  for (let i = 0; i < lines.length; i++) {
    if (isAttackChainHeading(lines[i])) {
      startIdx = i;
      break;
    }
  }
  if (startIdx === -1) return null;

  // 攻击链章节结束于下一个同级 `## ` 或更高级 `# ` 标题（进入新顶层报告区），或文档结尾。
  // 仅认 `## ` 会漏：evidence 区以 `# 一级标题`（如「# 注入利用报告」）开新大节，当其下
  // exploited 条目直接 `### VULN` 而无 `## ` 子标题时，攻击链章节会一路吞到文档尾，
  // 把整片 evidence 拖进 sectionMd → singleVulnMd(=before+after) 无任何 `### VULN`
  // → 单点漏洞计数归零（回归 NodeGoat-20260811-165637~1：删「## 已成功利用」后 total=0）。
  let endIdx = lines.length;
  for (let i = startIdx + 1; i < lines.length; i++) {
    if (/^#{1,2}\s/.test(lines[i])) {
      endIdx = i;
      break;
    }
  }

  const sectionLines = lines.slice(startIdx + 1, endIdx);
  let count = 0;
  for (const line of sectionLines) {
    if (LLM_CHAIN_HEADING_RE.test(line)) count++;
  }

  return {
    before: lines.slice(0, startIdx).join("\n"),
    sectionMd: sectionLines.join("\n"),
    after: lines.slice(endIdx).join("\n"),
    count,
  };
}

/**
 * 报告页 PoC 并入卡片（spec 2026-07-24-report-poc-inline-and-layout-fixes §3.1）：
 * - splitPocSection：把「# 可利用漏洞 PoC 集合」独立章节从报告 md 切出。后端 report
 *   endpoint（web/api/workspaces.py）把「主报告 + \n\n---\n\n + PoC md」拼成一份返回，
 *   前端要切出 PoC 章节以并入对应漏洞卡片、不再独立成章。
 * - parsePocEntries：解析「## 详细 PoC」下每条 `### ✓ ID · ...`，按 heading 提 vuln ID，
 *   建 {id, md} 映射，供 MarkdownView 把 PoC 并入对应漏洞卡片 body。
 *
 * 不变量：只做切分/解析，不把 PoC 条目解析为 vuln block、不经 parseVulnBlock、不进 vuln
 * segment。VULN_HEADING_RE / VULN_ID_RE 不动——PoC 的 ### heading 仅用于 ID 提取 + 并入，
 * 不当 vuln（PoC 是 externally_exploitable 漏洞的复现请求，依附于已存在的单点 vuln 卡片）。
 */

export interface PocSplit {
  /** PoC 章节之前的 md（主报告 + 攻击链；尾部 `---` 分隔线已剥离） */
  before: string;
  /** PoC 整章 md（自 `# 可利用漏洞 PoC 集合` 标题行起） */
  pocMd: string;
}

/**
 * 判断一行是否是 PoC 集合一级标题。
 * 命中条件：`^# ` 开头，标题文本（转小写、去空白后）含「poc集合」或「poccollection」，
 * 容错「# 可利用漏洞 PoC 集合（白盒/黑盒）」措辞变体。
 *
 * 脆弱点（显式记录）：依赖 poc_generator.render_poc_md 的标题措辞；若生成层改措辞需同步本规则。
 */
function isPocCollectionHeading(line: string): boolean {
  const m = /^#\s+(.+)$/.exec(line);
  if (!m) return false;
  const text = m[1].toLowerCase().replace(/\s+/g, "");
  return text.includes("poc集合") || text.includes("poccollection");
}

/**
 * 切出 PoC 独立章节。无 PoC（老报告 / 扫描未跑到 generate_poc_report）→ 返回 null。
 * before 尾部的 `---` 分隔线（后端 `\n\n---\n\n` 拼接产物）连同空行一并剥离，
 * 避免主报告末尾留孤立的 `---` 被渲染成水平线。
 */
export function splitPocSection(md: string): PocSplit | null {
  const lines = md.split(/\r?\n/);
  let idx = -1;
  for (let i = 0; i < lines.length; i++) {
    if (isPocCollectionHeading(lines[i])) {
      idx = i;
      break;
    }
  }
  if (idx === -1) return null;
  const beforeLines = lines.slice(0, idx);
  // 剥离尾部空行 + 后端拼接的 `---` 分隔线
  while (beforeLines.length > 0) {
    const last = beforeLines[beforeLines.length - 1].trim();
    if (last === "" || last === "---") beforeLines.pop();
    else break;
  }
  return {
    before: beforeLines.join("\n"),
    pocMd: lines.slice(idx).join("\n"),
  };
}

export interface PocEntry {
  /** vuln ID（从 PoC 条目 `### ✓ ID · ...` heading 提取），形如 INJ-VULN-01 / INJ-GN-08 */
  id: string;
  /** 条目体 md（去首行 heading；含 meta 行 + curl/Burp 代码块） */
  md: string;
}

/** PoC 条目 heading 里的 vuln ID（与 vuln-block 的 VULN_ID_RE 同源：PREFIX-VULN-NN / PREFIX-GN-NN）。 */
const POC_HEADING_ID_RE = /\b([A-Z]+-(?:VULN|GN)-\d+)\b/;

/** 定位「## 详细 PoC」二级标题的字符 offset（中英文容错）。无则 -1。 */
function findDetailPoCOffset(pocMd: string): number {
  const m = /^##\s+.*?(详细\s*PoC|detailed\s*poc).*$/im.exec(pocMd);
  return m ? m.index : -1;
}

/**
 * 解析 PoC md 的「## 详细 PoC」章节，按 `### ` 切条目，每条提 vuln ID。
 * - 无「## 详细 PoC」标题（格式异常 / 仅概览表）→ 返回 []（保守，不误并概览表内容进卡片）。
 * - 无 vuln ID 的 `###` 条目（纯描述 / 异常 heading）跳过，其内容忽略到下一个带 ID 的条目。
 * - 条目体首行 heading 已剥离；尾部 `---` 条目分隔线 + 空行剥离。
 */
export function parsePocEntries(pocMd: string): PocEntry[] {
  const offset = findDetailPoCOffset(pocMd);
  if (offset < 0) return [];
  const body = pocMd.slice(offset);
  const lines = body.split(/\r?\n/);
  const entries: PocEntry[] = [];
  let cur: { id: string; lines: string[] } | null = null;
  const flush = () => {
    if (!cur) return;
    const buf = [...cur.lines];
    while (buf.length > 0) {
      const last = buf[buf.length - 1].trim();
      if (last === "" || last === "---") buf.pop();
      else break;
    }
    const md = buf.join("\n").trim();
    if (md) entries.push({ id: cur.id, md });
    cur = null;
  };
  for (const line of lines) {
    if (/^###\s+/.test(line)) {
      flush();
      const m = POC_HEADING_ID_RE.exec(line);
      if (m) cur = { id: m[1], lines: [] };
      continue;
    }
    if (cur) cur.lines.push(line);
  }
  flush();
  return entries;
}
