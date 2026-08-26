import type { ParsedVulnBlock, ParsedVulnField, Severity } from "../api/types";

// 【T6 状态（spec 2026-08-26 §7.2）】报告页主路径已迁 ReportView（report_data.json
// 纯渲染，severity 由数据带出）；本文件的 severity 关键词推断（inferSeverity/
// baseSeverity）与 md 卡解析（parseVulnBlock/parseTableRowToBlock）只剩 md 降级
// 分支（ReportTab 404 回退，旧 scan 必须能渲染）与交付物页 md 预览在用。
// TODO(T6b)：md 降级分支下线时随 MarkdownView 一起删除。

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
 * 仅作旧报告兜底——新版四要素卡首行元信息带真数据 severity，由 parseMetaSeverity
 * 优先读取（见 spec 2026-08-25「severity 数据源」）。
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

/** 新版卡片元信息行（spec 2026-08-25 §5）中文严重程度词 → Severity。 */
const META_SEVERITY_ZH: Record<string, Severity> = {
  严重: "Critical",
  高危: "High",
  中危: "Medium",
  低危: "Low",
};

/** 新版卡片元信息行英文严重程度词 → Severity（F7b：en 报告同享真数据）。 */
const META_SEVERITY_EN: Record<string, Severity> = {
  Critical: "Critical",
  High: "High",
  Medium: "Medium",
  Low: "Low",
};

/** 元信息行 severity 片段（双语，全角/半角冒号容错）：
 * `严重程度：严重 ｜ CWE-95 ｜ …`（分组 1=中文词）或 `Severity: Critical ｜ …`（分组 2=英文词）。 */
const META_SEVERITY_RE =
  /严重程度[：:]\s*(严重|高危|中危|低危)|Severity[：:]\s*(Critical|High|Medium|Low)/;

/**
 * 从卡片元信息行读真数据 severity（新版四要素卡，报告渲染层写入，zh/en 双语）。
 * 匹配卡片 raw 内首个 `严重程度：严重|高危|中危|低危` 或 `Severity: Critical|High|Medium|Low`
 * → Severity；旧报告（无该行 / 老格式如 `- **严重程度:** high`）不匹配 → null，调用方落回启发式。
 */
export function parseMetaSeverity(block: ParsedVulnBlock): Severity | null {
  const m = META_SEVERITY_RE.exec(block.raw);
  if (!m) return null;
  // alternation 双分支互斥：zh 分支命中 → m[1] 有值（m[2] undefined）；en 分支反之
  return m[1] ? META_SEVERITY_ZH[m[1]] : META_SEVERITY_EN[m[2] ?? ""];
}

/**
 * 推断单条漏洞的危害等级（透明启发式，非权威评级）。
 *
 * 规则：
 *   0. 元信息行真数据优先：新版卡片首行 `严重程度：X`（parseMetaSeverity）非 null → 直接返回；
 *      以下启发式仅为旧报告兜底
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
  const meta = parseMetaSeverity(block);
  if (meta) return meta;

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
  // prefix 取类前缀：hm 命中标准 vuln heading 时从 id（=hm[1]）提取；hm 未命中（非标准
  // 标题、id 退化为整行文本）时为空。旧实现用 `id === fallbackTitle` 判断，会误伤纯 ID
  // 标题——### XSS-VULN-01 无描述时 id 与 fallbackTitle 都等于纯 ID → 判等 → prefix=""
  // → 该类漏洞全归空 prefix 组，报告页类型卡显示 0、还多出一张无标识的空组卡片。改以
  // hm 是否命中为准（回归 NodeGoat-20260729-194022 报告页「inject=6 / 其他 0 / 34 卡片」）。
  const prefix = hm ? (/^([A-Z]+)-/.exec(id)?.[1] ?? "") : "";
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

/** 判断一张表是否是「漏洞速查表」（渲染层确定性注入，表头 7 列：
 * ID/漏洞|Vulnerability/接口|Endpoint/参数|Parameters/严重度|Severity/验证|Verification/置信度|Confidence）。
 * 判定签名：表头含「接口/Endpoint」列且含「严重度/Severity」列——普通漏洞表格
 * （Injection Exploitation Queue / Authz 裁决概览）没有这两列。速查表行与同 ID 的
 * `### ` 完整卡并存，若照漏洞表提取会产生迷你块双计/每洞双卡/DOM id 重复
 * （终审 F1），故整表跳过留 prose（react-markdown 原样渲染）。 */
export function isSummaryTable(headers: string[]): boolean {
  const cols = headers.map((h) => h.trim().toLowerCase());
  const hasEndpoint = cols.some((c) => c === "接口" || c === "endpoint");
  const hasSeverity = cols.some((c) => c === "严重度" || c === "severity");
  return hasEndpoint && hasSeverity;
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
 * 「漏洞速查表」（isSummaryTable 命中：接口+严重度列）与普通表格
 * （如 `| 类型 | 数量 |`）整张原样留在 prose。
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
      if (isSummaryTable(headers)) {
        // 速查表（含接口+严重度列）→ 整表留 prose，不提取迷你块（防与 ### 完整卡
        // 双计/双卡/DOM id 重复，见 isSummaryTable 注释）
        proseAccum.push(...tableLines);
      } else if (isVulnTable(headers[0] ?? "", firstCell)) {
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
