import { useTranslation } from "react-i18next";
import { AlertCircle } from "lucide-react";
import { GroupLabel } from "@/components/GroupLabel";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { AuthFields, HostFields } from "@/components/ScanFormFields";
import type { CorrelationTopologyAnalysis, Repo, TopologyAuditLine, Workspace } from "@/api/types";
import type { AuthFormState, HostFormState } from "@/pages/ScanNewPage";
import type { CorrYamlError } from "@/lib/correlation-yaml";
import type { TopologyDraftState } from "@/lib/correlation-topology-draft";
import { RepositoryMultiSelector } from "./RepositoryMultiSelector";
import { CorrelationTopologyAnalysisPanel } from "./TopologyAnalysisPanel";
import { TopologyEditor } from "./TopologyEditor";
import { YamlPanel } from "./YamlPanel";

interface Props {
  workspace: string;
  wsList: Workspace[];
  onWorkspaceChange: (ws: string) => void;
  wsLoading: boolean;
  repos: Repo[];
  selectedRepos: string[];
  onSelectRepos: (repos: string[]) => void;
  analysis: CorrelationTopologyAnalysis | null;
  starting: boolean;
  analysisError: string | null;
  logLines: TopologyAuditLine[];
  logDropped?: number;
  onStart: () => void;
  onRetry: () => void;
  onCancel: () => void;
  onManual: () => void;
  topologyState: TopologyDraftState | null;
  onTopologyState: (state: TopologyDraftState) => void;
  onConfirm: () => void;
  availableRepos?: string[];
  onAddNode?: (repo: string) => void;
  onRemoveNode?: (repo: string) => void;
  scans: unknown[];
  yaml: string;
  onYaml: (y: string) => void;
  yamlError: CorrYamlError | null;
  gatewayUrl: string;
  onGatewayUrl: (v: string) => void;
  gatewayErr?: string | null;
  auth: AuthFormState;
  setAuth: (patch: Partial<AuthFormState>) => void;
  authErr?: string | null;
  host: HostFormState;
  setHost: (patch: Partial<HostFormState>) => void;
  hostErr?: string | null;
}

/** 分组容器：标题走共享 GroupLabel（coral 竖条 eyebrow——与手工模式/白盒表单统一）。 */
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="space-y-2.5"><GroupLabel>{title}</GroupLabel>{children}</section>;
}

export function CorrelationTopologyFields(props: Props) {
  const { t } = useTranslation();
  const wsEmpty = !props.wsLoading && props.wsList.length === 0;
  const confirmed = props.topologyState?.confirmation.status === "confirmed";
  return (
    <div className="flex flex-col gap-4">
      <Section title={t("scan.steps.workspace")}>
        <div className="space-y-1.5">
          <Select value={props.workspace} onValueChange={props.onWorkspaceChange}>
            <SelectTrigger className="w-full font-mono text-xs">
              <SelectValue placeholder={t("scan.fields.wsSelectPlaceholder")} />
            </SelectTrigger>
            <SelectContent>
              {props.wsList.map((ws) => <SelectItem key={ws.name} value={ws.name}>{ws.name}</SelectItem>)}
            </SelectContent>
          </Select>
          {wsEmpty && (
            <div className="flex items-center gap-1.5 text-xs text-amber">
              <AlertCircle className="h-3.5 w-3.5" />{t("scan.fields.wsEmptyHintUser")}
            </div>
          )}
        </div>
      </Section>

      <Section title={t("scan.correlation.analysis.selectRepos")}>
        {props.workspace ? (
          <RepositoryMultiSelector repos={props.repos} selected={props.selectedRepos} onChange={props.onSelectRepos}
            disabled={props.starting || props.analysis?.status === "running" || props.analysis?.status === "queued"} />
        ) : <p className="text-xs text-muted-foreground">{t("scan.fields.selectWsFirst")}</p>}
        <p className="text-[11px] text-muted-foreground">{t("scan.correlation.analysis.hint")}</p>
        <CorrelationTopologyAnalysisPanel analysis={props.analysis} starting={props.starting}
          error={props.analysisError} logLines={props.logLines} logDropped={props.logDropped}
          onStart={props.onStart} onRetry={props.onRetry}
          onCancel={props.onCancel} onManual={props.onManual} />
      </Section>

      {props.topologyState && (
        <Section title={t("scan.correlation.topology.editor")}>
          <TopologyEditor state={props.topologyState} onState={props.onTopologyState} scans={props.scans}
            availableRepos={props.availableRepos} onAddNode={props.onAddNode} onRemoveNode={props.onRemoveNode} />
          <div className="flex items-center gap-2">
            <Button type="button" onClick={props.onConfirm} disabled={confirmed}>{t("scan.correlation.topology.confirm")}</Button>
            {!confirmed && <span className="text-xs text-amber">{t("scan.correlation.topology.unconfirmed")}</span>}
          </div>
        </Section>
      )}

      {/* YAML 配置与拓扑同区块连续排布（2026-09-04 反馈「放到一块，中间别隔黑盒验证」）：
          synced 双向实时同步——图编辑实时派生文本、贴/改 YAML 即时重建图（无应用按钮）；
          无 border-t 分隔（连续性信号），与下方黑盒验证的分组隔断相区别。
          topologyState 未就绪（未跑分析）时也在此——直接贴合法 YAML 即长出拓扑。 */}
      <YamlPanel yaml={props.yaml} onChange={props.onYaml} error={props.yamlError} synced />

      <section className="space-y-2.5 border-t border-border pt-4">
        <GroupLabel>{t("scan.correlation.gatewayTitle")}</GroupLabel>
        <div className="space-y-1.5">
          <Label className="text-xs font-medium">{t("scan.correlation.gatewayLabel")}</Label>
          <Input value={props.gatewayUrl} onChange={(e) => props.onGatewayUrl(e.target.value)}
            placeholder={t("scan.correlation.gatewayPlaceholder")} size="sm" className="font-mono" />
          {props.gatewayErr && <div className="text-destructive text-xs">{props.gatewayErr}</div>}
        </div>
        <AuthFields value={props.auth} onChange={props.setAuth} workspace={props.workspace}
          authErr={props.authErr ?? null} refreshSignal={0} />
        <HostFields value={props.host} onChange={props.setHost} workspace={props.workspace} error={props.hostErr} />
      </section>
    </div>
  );
}
