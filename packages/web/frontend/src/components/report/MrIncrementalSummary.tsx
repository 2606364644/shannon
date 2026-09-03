import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, DoorOpen, ShieldAlert, Waypoints } from "lucide-react";
import type { IncrementalSummary, ReportScanMeta } from "@/api/types";

/** commit sha / ref 短显（>10 字符截前 10；分支名原样）。 */
function shortRef(ref?: string | null): string {
  if (!ref) return "—";
  return ref.length > 10 ? ref.slice(0, 10) : ref;
}

function StatCard({ icon, label, value, testid }: {
  icon: React.ReactNode; label: string; value: number | string; testid: string;
}) {
  return (
    <div
      data-testid={testid}
      className="flex items-center gap-2.5 rounded-md border border-border bg-muted/30 px-3 py-2.5"
    >
      <span className="text-muted-foreground" aria-hidden="true">{icon}</span>
      <div className="min-w-0">
        <div className="font-mono text-lg font-semibold leading-none text-foreground">{value}</div>
        <div className="mt-1 text-[11px] leading-tight text-muted-foreground">{label}</div>
      </div>
    </div>
  );
}

/**
 * MR 增量摘要段（spec 2026-09-03 §6）：报告顶部——base..head 头 + 三统计卡
 * （新增入口 / 删除防护 / 受影响链）+ 明细折叠区（来源 B 攻击面明细 / 来源 C
 * 防护行，followed_by_chains=false 标「未追链」供人审）。纯渲染，数据全由
 * report_data.incremental_summary 带出；仅 MR 扫描渲染（ReportView 条件挂载）。
 */
export function MrIncrementalSummary({ summary, scan }: {
  summary: IncrementalSummary; scan: ReportScanMeta;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const counts = summary.flow_counts ?? {};
  const stat = scan.diff_stat;
  const hasDetails =
    summary.new_entry_points.length > 0 || summary.removed_protections.length > 0;

  return (
    <section
      data-testid="mr-incremental-summary"
      className="space-y-3 rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)]"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 className="text-base font-semibold tracking-tight">{t("report.mr.title")}</h2>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-mono text-[11px] text-muted-foreground">
          <code title={`${scan.base_commit ?? ""}..${scan.head_commit ?? ""}`}>
            {shortRef(scan.base_commit)}..{shortRef(scan.head_commit)}
          </code>
          {stat && (
            <span>
              +{stat.insertions ?? 0} / −{stat.deletions ?? 0} · {stat.files ?? 0}{" "}
              {t("report.mr.files")}
            </span>
          )}
        </div>
      </div>

      {summary.degraded && (
        <div className="rounded-md border border-yellow/40 bg-yellow/10 px-2.5 py-1.5 text-xs text-yellow">
          {t("report.mr.degraded")}
        </div>
      )}

      <div className="grid gap-2 sm:grid-cols-3">
        <StatCard
          testid="mr-stat-new-entry"
          icon={<DoorOpen className="size-4" />}
          label={t("report.mr.newEntry")}
          value={counts.new_entry ?? 0}
        />
        <StatCard
          testid="mr-stat-removed-protection"
          icon={<ShieldAlert className="size-4" />}
          label={t("report.mr.removedProtections")}
          value={counts.removed_protection ?? 0}
        />
        <StatCard
          testid="mr-stat-affected-flows"
          icon={<Waypoints className="size-4" />}
          label={t("report.mr.affectedFlows")}
          value={counts.affected_flows ?? 0}
        />
      </div>

      {hasDetails && (
        <>
          <button
            type="button"
            data-testid="mr-details-toggle"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="inline-flex items-center gap-1 rounded-sm border border-border px-2 py-1 font-mono text-[10.5px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
          >
            <ChevronDown
              className={`size-3.5 transition-transform ${open ? "rotate-180" : ""}`}
              aria-hidden="true"
            />
            {t(open ? "report.mr.hideDetails" : "report.mr.showDetails")}
          </button>
          {open && (
            <div className="space-y-3">
              {summary.new_entry_points.length > 0 && (
                <div data-testid="mr-new-entry-details" className="space-y-1.5">
                  <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                    {t("report.mr.newEntry")}
                  </div>
                  <div className="overflow-hidden rounded-md border border-border">
                    {summary.new_entry_points.map((e) => (
                      <div
                        key={e.func_block_id}
                        className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-b border-border px-2.5 py-1.5 text-[12px] last:border-b-0"
                      >
                        {e.route && (
                          <code className="font-mono text-primary">
                            {e.method ? `${e.method} ` : ""}{e.route}
                          </code>
                        )}
                        {e.function && (
                          <span className="font-mono text-muted-foreground">{e.function}()</span>
                        )}
                        {e.authentication && (
                          <span className="text-muted-foreground">· {e.authentication}</span>
                        )}
                        {!e.route && !e.function && (
                          <code className="font-mono text-muted-foreground">{e.func_block_id}</code>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {summary.removed_protections.length > 0 && (
                <div data-testid="mr-removed-protection-details" className="space-y-1.5">
                  <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                    {t("report.mr.removedProtections")}
                  </div>
                  <div className="overflow-hidden rounded-md border border-border">
                    {summary.removed_protections.map((p, i) => (
                      <div
                        key={`${p.file}:${p.line}:${i}`}
                        className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-b border-border px-2.5 py-1.5 text-[12px] last:border-b-0"
                      >
                        <code className="font-mono text-muted-foreground">
                          {p.file}:{p.line}
                        </code>
                        {p.function && (
                          <span className="font-mono text-muted-foreground">{p.function}()</span>
                        )}
                        <span className="rounded-full border border-border px-1.5 py-px font-mono text-[10px] text-muted-foreground">
                          {p.kind || "protection"}
                        </span>
                        {p.followed_by_chains ? (
                          <span className="text-[11px] text-muted-foreground">
                            {t("report.mr.chainFollowed")}
                          </span>
                        ) : (
                          <span className="text-[11px] text-amber">
                            {t("report.mr.unfollowed")}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
