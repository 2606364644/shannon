import type { ParsedVulnBlock, Severity } from "../api/types";
import { inferSeverity, SEVERITY_RANK } from "./vuln-block";

/** severity → 实心背景色（堆叠条段 / 类型卡色条 / 左色条复用）。
 *  暖色梯度：Critical=red / High=orange / Medium=yellow / Low=灰；
 *  cyan 仅作信息色，不进 severity 编码（避免与 primary 撞色）。 */
export const SEVERITY_BG: Record<Severity, string> = {
  Critical: "bg-red",
  High: "bg-orange",
  Medium: "bg-yellow",
  Low: "bg-muted-foreground",
};

/** severity → 文字色（类型卡 range 文字 / 图例文字复用）。 */
export const SEVERITY_TEXT: Record<Severity, string> = {
  Critical: "text-red",
  High: "text-orange",
  Medium: "text-yellow",
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
  const order = typeSummaries?.map((t) => t.prefix) ?? [];
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
      const ts = typeSummaries?.find((t) => t.prefix === prefix);
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

  return {
    total: blocks.length,
    typeAggs,
    severityDist,
    publicCount: blocks.filter((b) => b.externallyExploitable === true).length,
    preAuthCount: blocks.filter((b) => b.authRequired === false).length,
    topRisks: topRisks.slice(0, 3),
  };
}
