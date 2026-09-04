import { useTranslation } from "react-i18next";
import { ChevronRight } from "lucide-react";
import { GroupLabel } from "@/components/GroupLabel";
import { Button } from "@/components/ui/button";
import type { CorrelationTopologyAnalysis, Repo, TopologyAuditLine } from "@/api/types";
import type { CorrView } from "@/pages/ScanNewPage";
import type { TopologyDraftState } from "@/lib/correlation-topology-draft";
import { RepositoryMultiSelector } from "./RepositoryMultiSelector";
import { CorrelationTopologyAnalysisPanel } from "./TopologyAnalysisPanel";
import { TopologyEditor } from "./TopologyEditor";

interface Props {
  workspace: string;
  repos: Repo[];
  selectedRepos: string[];
  onSelectRepos: (repos: string[]) => void;
  analysis: CorrelationTopologyAnalysis | null;
  starting: boolean;
  analysisError: string | null;
  historyEntries?: CorrelationTopologyAnalysis[];
  historyActiveId?: string | null;
  onSelectHistoryEntry?: (entry: CorrelationTopologyAnalysis) => void;
  logLines: TopologyAuditLine[];
  logDropped?: number;
  onStart: () => void;
  onRetry: () => void;
  onCancel: () => void;
  /** 「服务与分析」左轨道折叠开关（2026-09-04 工作台化：分析区块侧栏化，轨道整体
   *  可收起——手工用户收起即纯手工画布，画布占满全宽）。收起时页面层挂起
   *  latest/历史恢复查询（lazy），分析轮询不受影响。 */
  analysisOpen: boolean;
  onAnalysisOpen: (open: boolean) => void;
  topologyState: TopologyDraftState | null;
  onTopologyState: (state: TopologyDraftState) => void;
  /** 空态引导直达其他视图（表单/YAML 是同一拓扑的透镜）。 */
  onViewChange: (view: CorrView) => void;
  onRemoveNode: (repo: string) => void;
  scans: unknown[];
}

/** 图 tab（2026-09-04 工作台化）：左轨道（服务清单 + AI 分析 + 历史 = 拓扑的来源）+
 *  右主区（拓扑编辑器 = 拓扑本体）。原「全宽纵向堆叠」2535px 长页收进一屏工作台——
 *  输入→输出的流向从上下滚变成左右分区；确认门禁上移页面层 tabs 行（三视图共享），
 *  节点/边编辑统一进编辑器右栏属性面板（TopologyTables 撤除）。 */
export function CorrelationGraphTab(props: Props) {
  const { t } = useTranslation();
  return (
    <div className="grid gap-4 xl:grid-cols-[290px_minmax(0,1fr)]">
      {/* 左轨道：拓扑的来源——服务清单（勾选=参与拓扑）+ AI 分析 + 历史档案 */}
      <aside className="space-y-2.5" aria-label={t("scan.correlation.railTitle")}>
        <button
          type="button"
          data-testid="corr-analysis-toggle"
          onClick={() => props.onAnalysisOpen(!props.analysisOpen)}
          aria-expanded={props.analysisOpen}
          className="flex w-full items-center gap-2 text-left"
        >
          <GroupLabel>{t("scan.correlation.railTitle")}</GroupLabel>
          <ChevronRight
            className={`size-3.5 text-muted-foreground transition-transform ${props.analysisOpen ? "rotate-90" : ""}`}
            aria-hidden
          />
        </button>
        {props.analysisOpen && (
          <div className="space-y-2.5">
            {props.workspace ? (
              <RepositoryMultiSelector repos={props.repos} selected={props.selectedRepos}
                onChange={props.onSelectRepos}
                disabled={props.starting || props.analysis?.status === "running" || props.analysis?.status === "queued"} />
            ) : <p className="text-xs text-muted-foreground">{t("scan.fields.selectWsFirst")}</p>}
            <p className="text-[11px] text-muted-foreground">{t("scan.correlation.analysis.hint")}</p>
            <CorrelationTopologyAnalysisPanel analysis={props.analysis} starting={props.starting}
              error={props.analysisError} logLines={props.logLines} logDropped={props.logDropped}
              onStart={props.onStart} onRetry={props.onRetry}
              onCancel={props.onCancel}
              historyEntries={props.historyEntries} historyActiveId={props.historyActiveId}
              onSelectHistoryEntry={props.onSelectHistoryEntry} />
          </div>
        )}
      </aside>

      {/* 右主区：拓扑编辑器 / 空态引导 */}
      {props.topologyState ? (
        <section className="space-y-2.5">
          <GroupLabel>{t("scan.correlation.topology.editor")}</GroupLabel>
          <TopologyEditor state={props.topologyState} onState={props.onTopologyState} scans={props.scans}
            onRemoveNode={props.onRemoveNode} />
        </section>
      ) : (
        <div
          data-testid="corr-graph-empty"
          className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border px-4 py-8 text-center"
        >
          <p className="text-xs text-muted-foreground">{t("scan.correlation.graphEmptyTitle")}</p>
          <div className="flex flex-wrap items-center justify-center gap-2">
            <Button type="button" variant="outline" size="sm" onClick={() => props.onAnalysisOpen(true)}>
              {t("scan.correlation.graphEmptyAnalyze")}
            </Button>
            <Button type="button" variant="outline" size="sm" onClick={() => props.onViewChange("form")}>
              {t("scan.correlation.graphEmptyForm")}
            </Button>
            <Button type="button" variant="outline" size="sm" onClick={() => props.onViewChange("yaml")}>
              {t("scan.correlation.graphEmptyYaml")}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
