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

  // 攻击链章节结束于下一个 `## ` 二级标题，或文档结尾
  let endIdx = lines.length;
  for (let i = startIdx + 1; i < lines.length; i++) {
    if (/^##\s+/.test(lines[i])) {
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
