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

/** 识别漏洞条目标题行：`### <类前缀>(-<中段>)*-序号`。兼容双轨——LLM 轨 `-VULN-` 与
 * GitNexus 轨 `-GN-`/`-GN-EXPLORE-`/`-GN-LOGIC-`（双轨 ID 隔离防并集碰撞，来源另有字段承载）。 */
export const VULN_HEADING_RE = /^### ([A-Z]+)(?:-[A-Z]+)+-(\d+)\b/;

/** 识别漏洞 ID（表格首列、执行摘要引用等）：`<类前缀>(-<中段>)*-序号`，兼容双轨 -VULN-/-GN- 系列。 */
export const VULN_ID_RE = /^[A-Z]+(?:-[A-Z]+)+-\d+$/;

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

  // heading：### <完整ID> [sep] title ★ ...  ID = 大写类前缀(-大写中段)*-序号，兼容双轨
  // -VULN-（LLM 轨）与 -GN-/-GN-EXPLORE-/-GN-LOGIC-（GitNexus 轨）。保留原始 id 不变形——
  // inferSeverity 用 topRiskIds.has(id) 联动执行摘要，变形会断链（回归 hr_20260713-104726）。
  const hm = /^### ([A-Z]+(?:-[A-Z]+)+-\d+)\s*[—:：-]?\s*(.*)$/.exec(firstLine);
  const fallbackTitle = firstLine.replace(/^###\s+/, "");
  const id = hm?.[1] ?? fallbackTitle;
  const prefix = id === fallbackTitle ? "" : (/^([A-Z]+)-/.exec(id)?.[1] ?? "");
  let title = hm?.[2] ?? fallbackTitle;
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

// ── 漏洞表格解析（Injection Exploitation Queue / Authz 裁决概览）──

/** GFM 表格行：| ... |（至少含一个内部分隔 |）。 */
function isTableRow(line: string): boolean {
  const t = line.trim();
  return t.startsWith("|") && t.endsWith("|") && t.indexOf("|", 1) !== -1;
}

/** GFM 表格分隔行：| --- | :---: | 等（只含 | : - 空格，且含 -）。 */
function isTableSeparator(line: string): boolean {
  const t = line.trim();
  if (!t.includes("-")) return false;
  return /^\|?[\s:|-]+\|?$/.test(t);
}

/** 拆表格行为单元格（去首尾 |，按 | 分，trim）。 */
function splitTableRow(line: string): string[] {
  const t = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return t.split("|").map((c) => c.trim());
}

/** 判断一张表是否是漏洞表：首列头 = ID 且首列数据单元格形如 PREFIX-VULN-NN。 */
export function isVulnTable(headerFirstCell: string, firstDataCell: string): boolean {
  return headerFirstCell.trim().toLowerCase() === "id" && VULN_ID_RE.test(firstDataCell.trim());
}

/** 把漏洞表的一行（表头驱动）解析成 ParsedVulnBlock。 */
export function parseTableRowToBlock(headers: string[], row: string[]): ParsedVulnBlock {
  const colMap: Record<string, string> = {};
  headers.forEach((h, idx) => {
    colMap[h.trim().toLowerCase()] = (row[idx] ?? "").trim();
  });

  const id = colMap["id"] || "";
  const prefix = /^([A-Z]+)-/.exec(id)?.[1] ?? "";
  const vulnType =
    colMap["类型"] ?? colMap["type"] ?? colMap["vulnerability_type"] ?? "";

  const defect =
    colMap["核心缺陷"] ?? colMap["defect"] ?? colMap["描述"] ?? colMap["description"] ?? "";
  const source = colMap["源"] ?? colMap["source"] ?? "";
  const sink = colMap["sink"] ?? colMap["接收点"] ?? "";
  let title = defect || (source && sink ? `${source} → ${sink}` : "") || vulnType || id;
  title = title.replace(/`/g, "").trim();

  const authRaw = colMap["认证"] ?? colMap["auth"] ?? colMap["authentication"] ?? "";
  let authRequired: boolean | null = null;
  if (authRaw) {
    if (/pre-?auth|none|无认证|公开/i.test(authRaw)) authRequired = false;
    else if (/isloggedin|登录|需认证|required|认证/i.test(authRaw)) authRequired = true;
  }

  const confidence = normalizeConfidence(colMap["置信度"] ?? colMap["confidence"] ?? "");

  const fields: ParsedVulnField[] = headers
    .map((h, idx) => ({ key: h.trim(), val: (row[idx] ?? "").trim() }))
    .filter((f) => f.key.toLowerCase() !== "id" && f.val);

  return {
    id,
    prefix,
    title,
    starred: false,
    vulnType,
    fields,
    witnessPayload: undefined,
    externallyExploitable: null,
    authRequired,
    confidence,
    verdict: null,
    raw: `| ${row.join(" | ")} |`,
  };
}

/**
 * 从 prose 段文本里提取漏洞表格，返回交替的 prose/vuln 段序列。
 * 仅当表头首列 = ID 且首列数据单元格匹配 VULN_ID_RE 才拆成 vuln 段；
 * 普通表格（如 `| 类型 | 数量 |`）原样留在 prose。
 */
export function extractTableVulns(proseMd: string): Segment[] {
  const lines = proseMd.split(/\r?\n/);
  const out: Segment[] = [];
  let proseAccum: string[] = [];

  const flushProse = () => {
    if (proseAccum.length) {
      const text = proseAccum.join("\n");
      if (text.trim()) out.push({ type: "prose", md: text });
      proseAccum = [];
    }
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (isTableRow(line) && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const headers = splitTableRow(line);
      // 读整张表（连续表格行）
      const tableLines: string[] = [line, lines[i + 1]];
      let j = i + 2;
      while (j < lines.length && isTableRow(lines[j])) {
        tableLines.push(lines[j]);
        j++;
      }
      const dataRows = tableLines.slice(2).map(splitTableRow);
      const firstCell = dataRows[0]?.[0] ?? "";
      if (isVulnTable(headers[0] ?? "", firstCell)) {
        flushProse();
        for (const row of dataRows) {
          const block = parseTableRowToBlock(headers, row);
          out.push({ type: "vuln", raw: `| ${row.join(" | ")} |`, block });
        }
      } else {
        // 非漏洞表（首列非 ID）→ 整张表留 prose
        proseAccum.push(...tableLines);
      }
      i = j;
      continue;
    }
    proseAccum.push(line);
    i++;
  }
  flushProse();
  return out;
}

/**
 * 把整份报告 markdown 按 `### XXX-VULN-NN` 锚点切成段：
 * prose 段（无漏洞块的区域）与 vuln 段（单个漏洞块）交替。
 * vuln 段从 `### VULN` 行开始，到下一个 `#/##/###` 标题前结束。
 * 后处理：prose 段内的漏洞表格（首列=ID）也拆成 vuln 段，覆盖 Injection/Authz 表格形式。
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
  // 后处理：prose 段内的漏洞表格（首列=ID）拆成 vuln 段，
  // 覆盖 Injection Exploitation Queue / Authz 裁决概览这类表格形式。
  const expanded: Segment[] = [];
  for (const seg of segs) {
    if (seg.type === "prose") {
      expanded.push(...extractTableVulns(seg.md));
    } else {
      expanded.push(seg);
    }
  }
  return expanded;
}
