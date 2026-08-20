import { useEffect, useMemo, useState } from "react";
import { useParams, useOutletContext } from "react-router-dom";
import { useTranslation } from "react-i18next";
import useSWR from "swr";
import { ShieldAlert } from "lucide-react";
import { scanDeliverablesPath, blackboxRunDeliverablesPath, fetchDataflowView } from "../../api/client";
import { useApiJson } from "@/api/useApiResource";
import type { DeliverablesSummary, DeliverablesFile } from "../../api/types";
import { FileTree } from "../../components/FileTree";
import { VulnCard } from "../../components/VulnCard";
import { buildFindingTreeMap } from "@/components/dataflow/findingTreeMap";
import { ErrorState } from "../../components/ErrorState";
import { Empty } from "../../components/Empty";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { FileStage } from "./FileStage";

/**
 * 产物 tab（重设计 2026-08-17）：左树右预览 master-detail。
 * 左列 = 「漏洞聚合 · N」入口（回到聚合视图）+ 统一 FileTree（组合扫描的
 * 白盒/黑盒/融合三桶由 track 路径前缀自然成组，不再用独立的扁平桶列表）。
 * 右列 = 舞台：未选文件 → 漏洞聚合视图；选中文件 → FileStage 全宽预览。
 */
export function DeliverablesTab() {
  const { t } = useTranslation();
  const { workspace, scanId } = useParams<{ workspace: string; scanId: string }>();
  // 版本化 run（spec 2026-08-14）：selectedRun 非空时黑盒产物读该 run 的 deliverables。
  const outletCtx = useOutletContext<{ selectedRun?: string | null }>();
  const selectedRun = outletCtx?.selectedRun ?? null;
  const [sel, setSel] = useState<DeliverablesFile | null>(null);
  // SWR 数据层：key 即产物 path（run 选择派生）→ 切 tab 重挂载缓存即时显示；
  // path 变化（切 run）时重置文件选中。
  const path = workspace && scanId
    ? (selectedRun
        ? blackboxRunDeliverablesPath(workspace, scanId, selectedRun)
        : scanDeliverablesPath(workspace, scanId))
    : null;
  const { data, loading, error: err } = useApiJson<DeliverablesSummary>(path);
  useEffect(() => { setSel(null); }, [path]);

  // 数据流跳转映射（spec 2026-08-20 §5 路由与入口）：SWR 拉同一 dataflow API
  // （与 DataFlowTab 同 key → 共享缓存，零额外请求），建 finding_id → tree_id 传
  // VulnCard 展开态「查看数据流」链接。404（无产物）/ 失败 → 无映射 → 无链接（错误忽略）。
  const { data: dataflow } = useSWR(
    workspace && scanId ? ["dataflow", workspace, scanId] : null,
    () => fetchDataflowView(workspace!, scanId!),
  );
  const treeByFindingId = useMemo(() => buildFindingTreeMap(dataflow), [dataflow]);

  // 三态早返回：err → ErrorState；loading → Skeleton；data → 主布局
  if (err) return <ErrorState message={t("workspaceDetail.deliverables.loadError", { error: err })} />;
  if (loading) {
    return (
      <div className="space-y-2">
        {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-8 w-full" />)}
      </div>
    );
  }
  if (!data) return null;

  return (
    <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[280px_minmax(0,1fr)]">
      {/* 左列：聚合入口 + 文件树（独立滚动，窄屏堆叠在舞台上沿） */}
      <div className="max-h-[calc(100vh-220px)] space-y-3 overflow-auto border-b border-border pb-3 lg:border-b-0 lg:border-r lg:pb-0 lg:pr-4">
        <button
          type="button"
          onClick={() => setSel(null)}
          aria-current={sel ? undefined : "true"}
          className={`flex w-full items-center gap-1.5 rounded-sm p-1 text-left text-sm ${
            sel ? "bg-transparent hover:text-primary" : "bg-accent text-accent-foreground"
          }`}
        >
          <ShieldAlert className="size-3.5 shrink-0 text-red" aria-hidden />
          <span className="font-medium">
            {t("workspaceDetail.deliverables.aggTitle")} · {data.aggregated_vulnerabilities.length}
          </span>
        </button>
        <FileTree files={data.files} onSelect={setSel} selectedPath={sel?.path ?? null} />
      </div>
      {/* 右列：舞台——选中文件 → FileStage；否则漏洞聚合视图 */}
      <div className="min-w-0">
        {sel ? (
          <FileStage
            key={sel.path}
            ws={workspace!}
            scanId={scanId!}
            file={sel}
            runId={selectedRun}
            onBack={() => setSel(null)}
          />
        ) : (
          <AggregationView data={data} treeByFindingId={treeByFindingId} />
        )}
      </div>
    </div>
  );
}

/** 聚合视图：injection 标注 + VulnCard 堆叠（标题计数由左列「漏洞聚合 · N」入口承载，不重复）。
 *  treeByFindingId：finding_id → tree_id 映射（数据流跳转链接，spec 2026-08-20 §5）。 */
function AggregationView({
  data,
  treeByFindingId,
}: {
  data: DeliverablesSummary;
  treeByFindingId: Map<string, string>;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-2" data-testid="agg-view">
      {data.notes?.injection_has_no_queue && (
        /*
          injection 走 GitNexus 轨候选，不产独立 exploitation queue。
          用 Badge 的原生 title 属性做 tooltip，避免引入 Radix TooltipProvider
          横切（@/components/ui/tooltip 需 app 层 provider，本处不值得）。
        */
        <div>
          <Badge
            variant="outline"
            className="text-muted-foreground"
            title={t("workspaceDetail.deliverables.injectionHint")}
          >
            {t("workspaceDetail.deliverables.injectionBadge")}
          </Badge>
        </div>
      )}
      {data.aggregated_vulnerabilities.length === 0 && (
        <Empty title={t("workspaceDetail.deliverables.emptyTitle")} hint={t("workspaceDetail.deliverables.emptyHint")} />
      )}
      {data.aggregated_vulnerabilities.map((v) => (
        <VulnCard key={v.ID} v={v} dataflowTreeId={treeByFindingId.get(v.ID) ?? null} />
      ))}
    </div>
  );
}
