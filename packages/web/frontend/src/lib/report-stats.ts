import type { ParsedVulnBlock, Severity } from "../api/types";
import { inferSeverity, SEVERITY_RANK } from "./vuln-block";

// 【T6 状态（spec 2026-08-26 §7.2）】报告页主路径已迁 ReportView（吃 report_data.json
// 的确定性 stats，纯渲染）；本文件的推断/零计数补全（computeStats 第 146-164 行附近）
// 只剩 md 降级分支（ReportTab 404 回退）与交付物页 md 预览在用（经 MarkdownView
// -> ThreatOverview/TypeSummaryCards）。TODO(T6b)：md 降级分支下线时随 MarkdownView
// 一起删除，届时零计数类型由 report_data.stats.by_type 数据自带。

/** severity → 实心背景色（堆叠条段 / 类型卡色条 / 左色条复用）。
 *  暖色梯度：Critical=red / High=orange / Medium=yellow / Low=灰；
 *  cyan 仅作信息色，不进 severity 编码（避免与 primary 撞色）。 */
export const SEVERITY_BG: Record<Severity, string> = {
  Critical: "bg-red",
  High: "bg-orange",
  Medium: "bg-yellow",
  Low: "bg-muted-foreground",
};

/** severity → 文字色（类型卡 range 文字 / 图例文字复用）。文本步类（spec
 *  2026-08-27 §4）：向白/向黑混一档保全主题 AA——原 text-red 直用白底实测 3.8-4.4:1。 */
export const SEVERITY_TEXT: Record<Severity, string> = {
  Critical: "sev-text-red",
  High: "sev-text-orange",
  Medium: "sev-text-yellow",
  Low: "text-muted-foreground",
};

/** 类型 prefix → 规范显示名（markdown 原文大小写不齐，这里统一）。 */
export const TYPE_DISPLAY: Record<string, string> = {
  INJ: "Injection",
  XSS: "XSS",
  AUTH: "Auth",
  AUTHZ: "Authz",
  SSRF: "SSRF",
};

/** 反向映射：规范显示名(lowercase) → prefix。
 *  中文「数量:」类型汇总只给 displayName（Injection/XSS/...），prefix 缺失时反查补全，
 *  让零计数补全（`if (ts.prefix && …)`）能命中 → 全 5 类卡片正常渲染。 */
export const DISPLAY_TO_PREFIX: Record<string, string> = Object.fromEntries(
  Object.entries(TYPE_DISPLAY).map(([prefix, display]) => [display.toLowerCase(), prefix]),
);

/** 执行摘要「最高风险发现」单条。 */
export interface TopRiskItem {
  text: string;
  vulnIds: string[];
}

/** 「按漏洞类型汇总」节解析出的单类型摘要（findings 文字来源，best-effort）。 */
export interface ParsedTypeSummary {
  prefix: string;
  displayName: string;
  count: number;
  severityRangeRaw: string;
  findingsText?: string;
}

/** 单个类型的聚合数据。 */
export interface TypeAgg {
  prefix: string;
  displayName: string;
  count: number;
  severityRange: { min: Severity; max: Severity };
  severityRangeLabel: string;
  severityCounts: Record<Severity, number>;
  findingsText?: string;
}

/** 报告级统计，驱动 ThreatOverview + TypeSummaryCards。 */
export interface ReportStats {
  total: number;
  /** 攻击链条目数（attack-chain agent 产的 llm-chain-N）。computeStats 不算它（默认 0），
   *  实际值由 MarkdownView 用 splitAttackChainSection 的 count 覆盖。攻击链不进单点漏洞统计。 */
  attackChainCount: number;
  typeAggs: TypeAgg[];
  severityDist: Record<Severity, number>;
  publicCount: number;
  preAuthCount: number;
  topRisks: TopRiskItem[];
}

const SEV_BY_RANK: Severity[] = ["Low", "Medium", "High", "Critical"];

function emptyDist(): Record<Severity, number> {
  return { Critical: 0, High: 0, Medium: 0, Low: 0 };
}

/**
 * 从「全部漏洞块 + topRiskIds + topRisks」推导报告级统计。
 *
 * 结构性数据（total / count / severityDist / range / public / preAuth）全部走 blocks ×
 * inferSeverity 计算，自洽不依赖中文 prose；findingsText 走 typeSummaries（空则不渲染）。
 */
export function computeStats(
  blocks: ParsedVulnBlock[],
  topRiskIds: Set<string>,
  topRisks: TopRiskItem[],
  typeSummaries?: ParsedTypeSummary[],
): ReportStats {
  // 0. 给 typeSummaries 里 prefix 为空的项用 displayName 反查补全
  //    （中文「数量:」类型汇总只给 displayName，prefix 缺失 → 补全后零计数卡才能渲染）
  const summaries = typeSummaries?.map((ts) =>
    ts.prefix ? ts : { ...ts, prefix: DISPLAY_TO_PREFIX[ts.displayName.toLowerCase()] ?? "" },
  );

  // 1. 每个 block 的 severity
  const sevOf = new Map<string, Severity>();
  for (const b of blocks) sevOf.set(b.id, inferSeverity(b, topRiskIds));

  // 2. 全局 severity 分布
  const severityDist = emptyDist();
  for (const b of blocks) severityDist[sevOf.get(b.id) ?? "Medium"]++;

  // 3. 按 prefix 分组
  const byPrefix = new Map<string, ParsedVulnBlock[]>();
  for (const b of blocks) {
    const arr = byPrefix.get(b.prefix) ?? [];
    arr.push(b);
    byPrefix.set(b.prefix, arr);
  }

  // 排序：按 typeSummaries 的 markdown 出现顺序；缺省按 prefix 字母序
  const order = summaries?.map((t) => t.prefix) ?? [];
  const prefixOrder = (p: string) => {
    const idx = order.indexOf(p);
    return idx === -1 ? 999 : idx;
  };

  const typeAggs: TypeAgg[] = Array.from(byPrefix.keys())
    .sort((a, b) => prefixOrder(a) - prefixOrder(b) || a.localeCompare(b))
    .map((prefix) => {
      const items = byPrefix.get(prefix)!;
      const sevs = items.map((b) => sevOf.get(b.id) ?? "Medium");
      const ranks = sevs.map((s) => SEVERITY_RANK[s]);
      const max = SEV_BY_RANK[Math.max(...ranks) - 1];
      const min = SEV_BY_RANK[Math.min(...ranks) - 1];
      const severityCounts = emptyDist();
      for (const s of sevs) severityCounts[s]++;
      const ts = summaries?.find((t) => t.prefix === prefix);
      return {
        prefix,
        displayName: TYPE_DISPLAY[prefix] ?? ts?.displayName ?? prefix,
        count: items.length,
        severityRange: { min, max },
        severityRangeLabel: max === min ? max : `${max} ~ ${min}`,
        severityCounts,
        findingsText: ts?.findingsText,
      };
    });

  // 4. 补全 typeSummaries 中有但 blocks 中无的零计数类型（显示全部被测类型）
  if (summaries) {
    const existing = new Set(byPrefix.keys());
    for (const ts of summaries) {
      if (ts.prefix && !existing.has(ts.prefix)) {
        typeAggs.push({
          prefix: ts.prefix,
          displayName: TYPE_DISPLAY[ts.prefix] ?? ts.displayName ?? ts.prefix,
          count: 0,
          severityRange: { min: "Low" as Severity, max: "Low" as Severity },
          severityRangeLabel: "N/A",
          severityCounts: emptyDist(),
          findingsText: ts.findingsText,
        });
      }
    }
    // 重新排序确保零计数类型也在正确位置
    typeAggs.sort((a, b) => prefixOrder(a.prefix) - prefixOrder(b.prefix) || a.prefix.localeCompare(b.prefix));
  }

  return {
    total: blocks.length,
    // computeStats 只管单点漏洞，不知道攻击链 → 默认 0；MarkdownView 用实际值覆盖。
    attackChainCount: 0,
    typeAggs,
    severityDist,
    publicCount: blocks.filter((b) => b.externallyExploitable === true).length,
    preAuthCount: blocks.filter((b) => b.authRequired === false).length,
    topRisks: topRisks.slice(0, 3),
  };
}
