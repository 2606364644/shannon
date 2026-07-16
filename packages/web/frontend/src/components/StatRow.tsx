import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * 概览条 —— 列表页顶部的 stat 摘要行（workspaces / repos 共用）。
 * 4 格（窄屏 2 格），每格 = uppercase muted label + tabular-nums value（支持语义着色）。
 * 复用现有 token：border-border / bg-card 浮起卡 + --c-* 语义色。
 */
export type StatTone = "default" | "cyan" | "green" | "red";

export interface StatItem {
  label: string;
  value: ReactNode;
  tone?: StatTone;
}

const TONE_CLS: Record<StatTone, string> = {
  default: "",
  cyan: "text-cyan",
  green: "text-green",
  red: "text-red",
};

export function StatRow({ stats }: { stats: StatItem[] }) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {stats.map((s) => (
        <StatCard key={s.label} {...s} />
      ))}
    </div>
  );
}

function StatCard({ label, value, tone = "default" }: StatItem) {
  return (
    <div className="rounded-lg border border-border bg-card p-2.5">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={cn("mt-0.5 text-lg font-semibold tabular-nums", TONE_CLS[tone])}>{value}</div>
    </div>
  );
}
