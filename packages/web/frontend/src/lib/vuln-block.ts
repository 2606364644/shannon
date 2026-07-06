import type { ParsedVulnBlock, ParsedVulnField, Severity } from "../api/types";

/** Severity → 数值档位，便于加减调整后映射回 Severity。 */
export const SEVERITY_RANK = {
  Low: 1,
  Medium: 2,
  High: 3,
  Critical: 4,
} as const satisfies Record<Severity, number>;

const BY_RANK: Severity[] = ["Low", "Medium", "High", "Critical"];

function clampRank(r: number): number {
  return Math.max(SEVERITY_RANK.Low, Math.min(SEVERITY_RANK.Critical, r));
}

/**
 * base 等级：按 vulnerability_type + title 关键词匹配（中英双覆盖，大小写无关）。
 * 报告 markdown 无逐条 severity 字段，这是前端启发式推断——见 plan「severity 数据源」。
 */
function baseSeverity(vulnType: string, title: string): Severity {
  const t = `${vulnType} ${title}`.toLowerCase();
  const has = (kw: string) => t.includes(kw);

  // —— High ——
  if (has("command") || has("rce") || has("ssjs") || has("eval")) return "High";
  if (has("ssrf") || has("redirect") || has("开放重定向")) return "High";
  if (has("sqli") || has("nosql") || has("ssti") || has("lfi") || has("$where")) return "High";
  if (has("default") || has("hardcod") || has("明文") || has("plaintext") || has("默认凭据")) return "High";
  if (has("fixation") || has("不轮换")) return "High";
  if (has("secret") || has("签名")) return "High";
  if (has("transport") || has("https") || has("hsts") || has("明文 http")) return "High";
  if (has("cookie") && has("secure")) return "High";
  if (has("stored")) return "High";
  if (has("idor") || has("越权") || has("vertical") || has("horizontal") || has("isadmin") || has("未挂载")) return "High";

  // —— Medium ——
  if (has("reflected") || has("反射")) return "Medium";
  if (has("enumeration") || has("枚举")) return "Medium";
  if (has("weak") || has("弱口令") || has("弱策略")) return "Medium";
  if (has("abuse") || has("rate") || has("限流") || has("无限流") || has("锁定") || has("暴力")) return "Medium";
  if (has("csrf")) return "Medium";

  return "Medium"; // 兜底
}

/**
 * 推断单条漏洞的危害等级（透明启发式，非权威评级）。
 *
 * 规则：
 *   1. base：vulnType + title 关键词 → High / Medium
 *   2. +1 档：externally_exploitable==true && authentication_required==false（公网 pre-auth）
 *   3. -1 档：confidence=="low"（大小写容错）
 *   4. ★ 标题 或 id ∈ topRiskIds（执行摘要「最高风险发现」）→ 至少 High；若已 High → Critical
 *   5. clamp 到 {Low, Medium, High, Critical}
 *
 * @param block 解析后的漏洞块
 * @param topRiskIds 执行摘要「最高风险发现」里出现的 vuln ID 集合（可选）
 */
export function inferSeverity(block: ParsedVulnBlock, topRiskIds?: Set<string>): Severity {
  let rank = SEVERITY_RANK[baseSeverity(block.vulnType, block.title)];

  if (block.externallyExploitable === true && block.authRequired === false) {
    rank += 1;
  }

  const conf = block.confidence?.trim().toLowerCase();
  if (conf === "low") {
    rank -= 1;
  }

  if (block.starred || topRiskIds?.has(block.id)) {
    rank = rank >= SEVERITY_RANK.High ? SEVERITY_RANK.Critical : SEVERITY_RANK.High;
  }

  return BY_RANK[clampRank(rank) - 1];
}

// ── markdown 块切分 + 解析 ──────────────────────────────────────────

/** 识别漏洞条目标题行：`### PREFIX-VULN-NUM`（PREFIX 全大写字母）。 */
export const VULN_HEADING_RE = /^### ([A-Z]+)-VULN-(\d+)\b/;

/** 切分产物：prose 段（原样喂 react-markdown）或 vuln 段（解析后的块）。 */
export type Segment =
  | { type: "prose"; md: string }
  | { type: "vuln"; raw: string; block: ParsedVulnBlock };

function normalizeConfidence(raw: string): "high" | "med" | "low" | null {
  const c = raw.trim().toLowerCase();
  if (c === "high") return "high";
  if (c === "med" || c === "medium") return "med";
  if (c === "low") return "low";
  return null;
}

/**
 * 解析单个漏洞块（`### XXX-VULN-NN — title ★ ...` + 后续 kv-list）。
 * 关键字段（externally_exploitable 等）用正则从整个 raw 提取，鲁棒于 `|` 同行多字段写法。
 */
export function parseVulnBlock(raw: string): ParsedVulnBlock {
  const lines = raw.split(/\r?\n/);
  const firstLine = lines[0] ?? "";

  // heading：### PREFIX-VULN-NUM [sep] title ★ ...
  const hm = /^### ([A-Z]+)-VULN-(\d+)\s*[—:：-]?\s*(.*)$/.exec(firstLine);
  const prefix = hm?.[1] ?? "";
  const num = hm?.[2] ?? "";
  const fallbackTitle = firstLine.replace(/^###\s+/, "");
  const id = prefix && num ? `${prefix}-VULN-${num}` : fallbackTitle;
  let title = hm?.[3] ?? fallbackTitle;
  const starred = /★/.test(title);
  title = title.replace(/★.*$/, "").trim();

  // kv-list（冒号守卫：`- **key:** val`；多字段行只取首字段，与 prose kv-row 一致）
  const fields: ParsedVulnField[] = [];
  for (const line of lines.slice(1)) {
    const fm = /^-\s+\*\*([^*]+)[:：]\*\*\s*(.*)$/.exec(line.trim());
    if (fm) fields.push({ key: fm[1].trim(), val: fm[2].trim() });
  }

  // witness_payload：优先 field val 反引号 code；否则块内第一个 fenced code
  const wpField = fields.find((f) => f.key === "witness_payload");
  let witnessPayload: string | undefined;
  if (wpField) {
    const cm = wpField.val.match(/`([^`]+)`/);
    if (cm) witnessPayload = cm[1];
    else if (wpField.val.trim()) witnessPayload = wpField.val.trim();
  }
  if (!witnessPayload) {
    const fence = /```[a-zA-Z]*\r?\n([\s\S]*?)```/.exec(raw);
    if (fence) witnessPayload = fence[1].trim() || undefined;
  }

  // 关键字段正则提取（从整个 raw，不受 | 同行多字段干扰）
  const ext = /\*\*externally_exploitable:\*\*\s*(true|false)/i.exec(raw);
  const auth = /\*\*authentication_required:\*\*\s*(true|false)/i.exec(raw);
  const conf = /\*\*confidence:\*\*\s*(high|medium|med|low)/i.exec(raw);
  const verd = /\*\*verdict:\*\*\s*(vulnerable|safe|exploited)/i.exec(raw);
  const typ = /\*\*vulnerability_type:\*\*\s*(.+)/i.exec(raw);

  return {
    id,
    prefix,
    title,
    starred,
    vulnType: typ ? typ[1].replace(/`/g, "").replace(/\|.*$/, "").trim() : "",
    fields,
    witnessPayload,
    externallyExploitable: ext ? ext[1].toLowerCase() === "true" : null,
    authRequired: auth ? auth[1].toLowerCase() === "true" : null,
    confidence: conf ? normalizeConfidence(conf[1]) : null,
    verdict: verd ? verd[1].toLowerCase() : null,
    raw,
  };
}

/**
 * 把整份报告 markdown 按 `### XXX-VULN-NN` 锚点切成段：
 * prose 段（无漏洞块的区域）与 vuln 段（单个漏洞块）交替。
 * vuln 段从 `### VULN` 行开始，到下一个 `#/##/###` 标题前结束。
 */
export function splitByVulnBlocks(md: string): Segment[] {
  const lines = md.split(/\r?\n/);
  const segs: Segment[] = [];
  let prose: string[] = [];
  let vuln: string[] | null = null;

  const flushProse = () => {
    if (prose.length) {
      const text = prose.join("\n");
      if (text.trim()) segs.push({ type: "prose", md: text });
      prose = [];
    }
  };
  const flushVuln = () => {
    if (vuln) {
      const raw = vuln.join("\n");
      segs.push({ type: "vuln", raw, block: parseVulnBlock(raw) });
      vuln = null;
    }
  };

  for (const line of lines) {
    if (VULN_HEADING_RE.test(line)) {
      flushProse();
      flushVuln();
      vuln = [line];
    } else if (vuln !== null && /^#{1,3}\s/.test(line)) {
      // vuln 段内遇到其他 #/##/### 标题 → 结束 vuln
      flushVuln();
      prose = [line];
    } else if (vuln !== null) {
      vuln.push(line);
    } else {
      prose.push(line);
    }
  }
  flushProse();
  flushVuln();
  return segs;
}
