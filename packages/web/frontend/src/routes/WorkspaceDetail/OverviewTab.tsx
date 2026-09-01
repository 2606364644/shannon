import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { SessionMetrics } from "../../api/types";
import { fmtCost } from "../../utils/currency";
import { StatusBadge } from "../../components/StatusBadge";
import { ErrorState } from "../../components/ErrorState";
import { Empty } from "../../components/Empty";
import { Badge } from "@/components/ui/badge";
import { Card, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { useScanDetail } from "./useScanDetail";
import { CorrelationOverview } from "./CorrelationOverview";

function fmtMs(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return "—";
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function OverviewTab() {
  const { t } = useTranslation();
  const { workspace, scanId } = useParams<{ workspace: string; scanId: string }>();
  // SWR 数据层（2026-08-17 批次 Task 2）：与 ScanDetail / ReportTab 共享 key，单请求。
  const { data: s, loading, error: err } = useScanDetail(workspace, scanId);

  if (err) return <ErrorState message={t("workspaceDetail.overview.loadError", { error: err })} />;
  if (loading) {
    return (
      <div className="space-y-2">
        {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-8 w-full" />)}
      </div>
    );
  }
  // 跨仓关联主行（D6）：概览 = 简版 CorrelationOverview（三段横幅 + 子仓状态网格）。
  // 主行 session 无常规 phases/agents metrics（编排行不跑 agent），瀑布/Agent 台账
  // 对关联行是空壳——按 scan_type 整体切换而非共用。
  if (s?.scan_type === "correlation") {
    return <CorrelationOverview ws={workspace ?? ""} scanId={scanId ?? ""} />;
  }
  if (!s?.metrics) {
    return <Empty title={t("workspaceDetail.overview.waitTitle")} hint={t("workspaceDetail.overview.waitHint")} />;
  }
  const m = s.metrics;
  // 初始态守卫:session.py create_workspace 写 {"agents":{}}(truthy 空对象)会绕过上层 !s.metrics;
  // phases 缺失 + agents 空 = pre-recon 未完成 → 显「等待扫描」空态而非渲染崩溃。
  const hasRealData =
    Object.keys(m.phases ?? {}).length > 0 || Object.keys(m.agents ?? {}).length > 0;
  if (!hasRealData) {
    return <Empty title={t("workspaceDetail.overview.waitTitle")} hint={t("workspaceDetail.overview.waitHint")} />;
  }

  const statusConflict = !!(s.status && s.session?.status && s.status !== s.session.status);

  return (
    <div className="space-y-5">
      {/* status bar */}
      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <StatusBadge status={s.status ?? s.session?.status ?? "?"} />
          <span>{s.scan_type}</span>
          <span className="font-mono text-muted-foreground">{s.repo_path}</span>
          {/*
            前端 status 矛盾兜底渲染（黄色 Badge）。
            后端归一层已对 mismatch 做了 flag；此处仅作可视化提示，不修改状态语义。
          */}
          {statusConflict && (
            <Badge variant="outline" className="border-yellow/40 text-yellow">
              {t("workspaceDetail.overview.statusConflict", { topLevel: s.status, sessionLevel: s.session!.status })}
            </Badge>
          )}
        </div>
      </Card>

      {/* big numbers */}
      <Card className="p-4">
        <div className="grid grid-cols-3 gap-6 font-mono">
          <div>
            <div className="text-2xl font-bold text-foreground">{fmtCost(m.total_cost_usd, m.cost_currency)}</div>
            <div className="text-xs text-muted-foreground">{t("workspaceDetail.overview.bigCost")}</div>
          </div>
          <div>
            <div className="text-2xl font-bold text-foreground">{fmtMs(m.total_duration_ms)}</div>
            <div className="text-xs text-muted-foreground">{t("workspaceDetail.overview.bigDuration")}</div>
          </div>
          <div>
            <div className="text-2xl font-bold text-foreground">{Object.keys(m.agents ?? {}).length}</div>
            <div className="text-xs text-muted-foreground">{t("workspaceDetail.overview.bigAgents")}</div>
          </div>
        </div>
      </Card>

      <PhaseWaterfall phases={m.phases} fmt={fmtMs} />
      <AgentTable agents={m.agents} fmt={fmtMs} />
    </div>
  );
}

function PhaseWaterfall({ phases, fmt }: { phases: SessionMetrics["phases"]; fmt: (ms: number) => string }) {
  const { t } = useTranslation();
  const entries = Object.entries(phases ?? {});
  return (
    <Card className="p-4">
      <CardTitle className="mb-2 font-semibold tracking-tight text-base">{t("workspaceDetail.overview.phaseWaterfall")}</CardTitle>
      <div className="flex items-end gap-0.5 h-20">
        {entries.map(([name, p]) => {
          // 阶段成败（2026-09-01）：agent 最终失败 → 着色（全失败红 / 部分失败黄，
          // 与 ScanProgressOverview 语义色同源）；旧 session 无字段 → ?? 0 = 现行为。
          const failed = p.failed_agent_count ?? 0;
          const barCls =
            failed > 0 && failed >= p.agent_count ? "bg-red"
            : failed > 0 ? "bg-yellow"
            : "bg-cyan";
          return (
            <div
              key={name}
              className={`${barCls} min-w-[60px] p-1 text-background rounded-t-sm overflow-hidden`}
              style={{ width: `${p.duration_percentage}%` }}
              title={`${name}: ${p.duration_percentage}%${failed > 0 ? ` · ${t("workspaceDetail.overview.phaseFailedTitle", { failed, count: p.agent_count })}` : ""}`}
            >
              <div className="text-xs font-bold truncate">{name}</div>
              <div className="text-[0.7rem] opacity-85 font-mono">
                {failed > 0 && <span className="font-bold">✗{failed}/{p.agent_count} · </span>}
                {p.duration_percentage}% · {fmt(p.duration_ms)} · {fmtCost(p.cost_usd, p.cost_currency)} · {t("workspaceDetail.overview.phaseAgents", { count: p.agent_count })}
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function AgentTable({ agents, fmt }: { agents: SessionMetrics["agents"]; fmt: (ms: number) => string }) {
  const { t } = useTranslation();
  return (
    <Card className="overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <CardTitle className="font-semibold tracking-tight text-sm">{t("workspaceDetail.overview.agentLedger")}</CardTitle>
        {/* 尝试列图例（2026-09-01 显式化）：字形取代纯颜色隐式语义 */}
        <span className="font-mono text-[0.7rem] text-muted-foreground" data-testid="agent-table-legend">
          <span className="text-green">✓</span> {t("workspaceDetail.overview.agentTable.legendSuccess")}
          <span aria-hidden className="opacity-40"> · </span>
          <span className="text-yellow">⚠</span> {t("workspaceDetail.overview.agentTable.legendRetried")}
          <span aria-hidden className="opacity-40"> · </span>
          <span className="text-red">✗</span> {t("workspaceDetail.overview.agentTable.legendFailed")}
        </span>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("workspaceDetail.overview.agentTable.agent")}</TableHead>
            <TableHead>{t("workspaceDetail.overview.agentTable.duration")}</TableHead>
            <TableHead>{t("workspaceDetail.overview.agentTable.cost")}</TableHead>
            <TableHead>{t("workspaceDetail.overview.agentTable.attempt")}</TableHead>
            <TableHead>{t("workspaceDetail.overview.agentTable.model")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {Object.entries(agents ?? {}).map(([name, a]) => {
            const failed = a.success === false;
            const warned = a.attempt_number > 1 || !!a.error;
            // 显式字形（2026-09-01）：✗ 最终失败（红）/ ⚠ 重试后成功（黄）/ ✓ 一次成功（绿）
            const attemptCls = failed ? "text-red" : warned ? "text-yellow" : "text-green";
            const glyph = failed ? "✗" : warned ? "⚠" : "✓";
            const attemptText = `${glyph} ${a.attempt_number}${a.error ? `(${a.error.slice(0, 20)})` : ""}`;
            return (
              <TableRow key={name}>
                <TableCell className="font-mono">{name}</TableCell>
                <TableCell className="font-mono">{fmt(a.duration_ms)}</TableCell>
                <TableCell className="font-mono">{fmtCost(a.cost_usd, a.cost_currency)}</TableCell>
                <TableCell className={`whitespace-nowrap font-mono ${attemptCls}`}>{attemptText}</TableCell>
                <TableCell className="whitespace-nowrap font-mono text-xs text-muted-foreground">{a.model}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </Card>
  );
}
