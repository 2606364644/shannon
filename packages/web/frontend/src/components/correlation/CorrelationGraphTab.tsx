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
  /** 「自动分析」折叠区块开关（2026-09-04 tabs 重组：模式概念删除，AI 分析收进图 tab）。
   *  收起时页面层挂起 latest/历史恢复查询（lazy），分析轮询不受影响。 */
  analysisOpen: boolean;
  onAnalysisOpen: (open: boolean) => void;
  topologyState: TopologyDraftState | null;
  /** 拓扑带 AI 分析来源（哪怕手工改过）→ 须显式确认才能提交；纯手搭免确认。 */
  needsConfirm: boolean;
  /** 页面层确认态（fingerprint + YAML canonical 语义比对全通过）。 */
  confirmed: boolean;
  onTopologyState: (state: TopologyDraftState) => void;
  onConfirm: () => void;
  /** 空态引导直达其他视图（表单/YAML 是同一拓扑的透镜）。 */
  onViewChange: (view: CorrView) => void;
  availableRepos?: string[];
  onAddNode?: (repo: string) => void;
  onRemoveNode?: (repo: string) => void;
  scans: unknown[];
}

/** 图 tab：自动分析折叠区块（可选来源）+ 拓扑编辑器 + 确认条 + 空态三分支引导。
 *  2026-09-04 tabs 重组前是 CorrelationTopologyFields（auto 模式整页容器）——模式分页
 *  删除后图只是三视图之一，AI 分析降级为图上方一个可折叠工具区块。 */
export function CorrelationGraphTab(props: Props) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-4">
      {/* ① 自动分析（可折叠，默认展开——自动分析是主路径；手工用户收起即纯手工） */}
      <section className="space-y-2.5">
        <button
          type="button"
          data-testid="corr-analysis-toggle"
          onClick={() => props.onAnalysisOpen(!props.analysisOpen)}
          aria-expanded={props.analysisOpen}
          className="flex w-full items-center gap-2 text-left"
        >
          <GroupLabel>{t("scan.correlation.analysis.sectionTitle")}</GroupLabel>
          <ChevronRight
            className={`size-3.5 text-muted-foreground transition-transform ${props.analysisOpen ? "rotate-90" : ""}`}
            aria-hidden
          />
        </button>
        {props.analysisOpen && (
          <div className="space-y-2">
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
      </section>

      {/* ② 拓扑编辑器 / 空态引导 */}
      {props.topologyState ? (
        <section className="space-y-2.5">
          <GroupLabel>{t("scan.correlation.topology.editor")}</GroupLabel>
          <TopologyEditor state={props.topologyState} onState={props.onTopologyState} scans={props.scans}
            availableRepos={props.availableRepos} onAddNode={props.onAddNode} onRemoveNode={props.onRemoveNode} />
          {props.needsConfirm && (
            <div className="flex items-center gap-2">
              <Button type="button" onClick={props.onConfirm} disabled={props.confirmed}>
                {t("scan.correlation.topology.confirm")}
              </Button>
              {!props.confirmed && <span className="text-xs text-amber">{t("scan.correlation.topology.unconfirmed")}</span>}
            </div>
          )}
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
