import { useTranslation } from "react-i18next";
import { Info, Trash2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { RepoCombobox } from "@/components/RepoCombobox";
// 认证/HOST 块复用既有抽取组件（auth-profile-vault Task 14 / HOST Task 13 已抽出的共享
// AuthFields/HostFields——白盒组合扫描同款），保证字段映射与 assignAuthToBody/assignHostToBody 一致。
import { AuthFields, HostFields } from "@/components/ScanFormFields";
import { useRepos } from "@/api/useRepos";
import { useScans } from "@/routes/WorkspaceDetail/useScans";
import type { Workspace } from "@/api/types";
import type { AuthFormState, HostFormState } from "@/pages/ScanNewPage";
import { validateForm, type CorrFormState, type CorrRepoDraft, type CorrRole, type CorrProtocol, type CorrYamlError } from "@/lib/correlation-yaml";
import { YamlPanel } from "./YamlPanel";
import { formatCorrIssue } from "./corr-issues-i18n";

interface Props {
  state: CorrFormState;
  /** 表单交互路径：父层 setCorrState(s) + 重生成 yaml（yaml 是派生态——单向数据流）。 */
  onState: (s: CorrFormState) => void;
  yaml: string;
  /** YAML 编辑路径：仅向上抛文本，父层校验（不实时回填表单）。 */
  onYaml: (y: string) => void;
  yamlError: CorrYamlError | null;
  /** 显式「应用到表单」：yamlToForm 回填 state（YamlPanel 内按钮，非实时）。 */
  onApplyYaml: () => void;
  workspace: string;
  wsList: Workspace[];
  onWorkspaceChange: (ws: string) => void;
  wsLoading: boolean;
  /** 黑盒验证网关地址（复用页面 FormState.url 承载，避免新 state）。 */
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

/** 分组小标题：coral 竖条 eyebrow（与 ScanFormFields 的 GroupLabel 同视觉语言——
 *  该 helper 未导出，此处内嵌同结构副本）。 */
function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="h-3 w-[3px] rounded-full bg-primary" aria-hidden />
      <span className="text-[11px] font-semibold text-muted-foreground">{children}</span>
    </div>
  );
}

/** 紧凑 segmented（角色/来源二选一）：aria-pressed 按钮，样式对齐 ScanFormFields 的来源 segmented。 */
function MiniSegmented({ value, options, onChange }: {
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
}) {
  return (
    <div className="inline-flex items-center gap-1 rounded-lg border border-border bg-muted/40 p-1 w-full">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          aria-pressed={value === o.value}
          className={`flex-1 px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
            value === o.value ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** 已命名的 entrypoint 仓库名（星型边的 from 端）；无则 null。 */
function entryNameOf(s: CorrFormState): string | null {
  const e = s.repos.find((r) => r.role === "entrypoint" && r.repo.trim());
  return e ? e.repo : null;
}

/** 星型边自动补齐：backend 卡片命名后若无任何边触达且存在已命名 entrypoint → 补
 *  entrypoint→该仓库 一条边（协议取卡片 protocol）。幂等（已有边不重复补）。 */
function ensureStarEdge(s: CorrFormState, idx: number): CorrFormState {
  const card = s.repos[idx];
  if (!card || card.role !== "backend" || !card.repo.trim()) return s;
  const from = entryNameOf(s);
  if (!from || from === card.repo) return s;
  if (s.relations.some((e) => e.from === card.repo || e.to === card.repo)) return s;
  return { ...s, relations: [...s.relations, { from, to: card.repo, protocol: card.protocol }] };
}

function fmtTime(unix?: number): string {
  if (!unix) return "—";
  return new Date(unix * 1000).toLocaleString();
}

export function CorrelationFormFields({
  state,
  onState,
  yaml,
  onYaml,
  yamlError,
  onApplyYaml,
  workspace,
  wsList,
  onWorkspaceChange,
  wsLoading,
  gatewayUrl,
  onGatewayUrl,
  gatewayErr,
  auth,
  setAuth,
  authErr,
  host,
  setHost,
  hostErr,
}: Props) {
  const { t } = useTranslation();
  // repo 候选（卡片 RepoCombobox 数据源）：SWR 共享 key，与 ReposTab 同 ["repos", ws] 缓存。
  const { repos } = useRepos(workspace);
  // 复用候选：当前 ws 的 scans（useScans 共享 ["scans", ws] 缓存），按卡片仓库过滤 whitebox。
  const { scans } = useScans(workspace || undefined);

  // —— 卡片操作（全部经 onState 上抛，父层重生成 YAML） ——
  function addRepo() {
    const next: CorrRepoDraft = {
      repo: "",
      // role 逻辑（brief）：无 entrypoint 时默认 entrypoint，否则 backend（星型边在命名/角色
      // 变更时由 ensureStarEdge 补——边引用仓库名，须等卡片命名）。
      role: state.repos.some((r) => r.role === "entrypoint") ? "backend" : "entrypoint",
      protocol: "grpc",
      reuseScanId: null,
    };
    onState({ ...state, repos: [...state.repos, next] });
  }

  function removeRepo(i: number) {
    const name = state.repos[i]?.repo ?? "";
    onState({
      repos: state.repos.filter((_, j) => j !== i),
      // 删除卡片 → 清掉引用该仓库名的关联边
      relations: state.relations.filter((e) => e.from !== name && e.to !== name),
    });
  }

  function setCardName(i: number, name: string) {
    const old = state.repos[i]?.repo ?? "";
    let s: CorrFormState = {
      ...state,
      repos: state.repos.map((r, j) => (j === i ? { ...r, repo: name } : r)),
      // 仓改名 → 同步改写引用旧名的边（保住已建的拓扑）
      relations: state.relations.map((e) => ({
        ...e,
        from: e.from === old && name ? name : e.from,
        to: e.to === old && name ? name : e.to,
      })),
    };
    s = ensureStarEdge(s, i);
    onState(s);
  }

  function setCardRole(i: number, role: CorrRole) {
    let s: CorrFormState = { ...state, repos: state.repos.map((r, j) => (j === i ? { ...r, role } : r)) };
    s = ensureStarEdge(s, i);
    onState(s);
  }

  function setCardSource(i: number, mode: "rescan" | "reuse") {
    onState({
      ...state,
      repos: state.repos.map((r, j) =>
        j === i ? { ...r, reuseScanId: mode === "reuse" ? (r.reuseScanId ?? "") : null } : r),
    });
  }

  function setCardReuseScan(i: number, scanId: string) {
    onState({ ...state, repos: state.repos.map((r, j) => (j === i ? { ...r, reuseScanId: scanId } : r)) });
  }

  function setCardProtocol(i: number, protocol: CorrProtocol) {
    const target = state.repos[i]?.repo ?? "";
    const from = entryNameOf(state);
    onState({
      ...state,
      repos: state.repos.map((r, j) => (j === i ? { ...r, protocol } : r)),
      // 自动补的星型边（entrypoint→该仓库）跟随卡片协议，保持摘要与 YAML 一致
      relations: state.relations.map((e) =>
        e.to === target && e.from === from ? { ...e, protocol } : e),
    });
  }

  // 复用候选（brief）：当前 ws 的 whitebox 扫描 + repo === 卡片仓库名
  const candidatesFor = (card: CorrRepoDraft) =>
    card.repo.trim() ? scans.filter((s) => s.scan_type === "whitebox" && s.repo === card.repo) : [];

  const issues = validateForm(state);

  // workspace 选择器——与 ScanFormFields 的 wsSelectInner 同结构副本（该块未导出；两处
  // 各自内联，行为/样式保持一致，避免本次任务对 ScanFormFields 做侵入式抽取）。
  const wsEmpty = !wsLoading && wsList.length === 0;

  return (
    <div className="space-y-5">
      {/* ① 工作区 */}
      <section className="space-y-2">
        <SectionLabel>{t("scan.fields.wsSelectLabel")}</SectionLabel>
        <div className="space-y-1.5">
          <Select value={workspace} onValueChange={onWorkspaceChange}>
            <SelectTrigger className="w-full font-mono text-xs">
              <SelectValue placeholder={t("scan.fields.wsSelectPlaceholder")} />
            </SelectTrigger>
            <SelectContent>
              {wsEmpty ? (
                <SelectItem value="__empty__" disabled>{t("scan.fields.wsEmptyOption")}</SelectItem>
              ) : wsList.map((w) => (
                <SelectItem key={w.name} value={w.name}>{w.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </section>

      {/* ② 仓库卡片列表 */}
      <section className="space-y-2.5">
        <SectionLabel>{t("scan.correlation.reposSection")}</SectionLabel>
        {!workspace ? (
          <div className="text-xs text-muted-foreground">{t("scan.fields.selectWsFirst")}</div>
        ) : (
          <>
            {state.repos.map((card, i) => {
              const candidates = candidatesFor(card);
              return (
                <div key={i} data-testid="corr-repo-card" className="rounded-lg border border-border bg-secondary p-3 space-y-2.5">
                  <div className="flex items-start gap-2">
                    <div className="flex-1 min-w-0">
                      <RepoCombobox
                        repos={repos}
                        value={card.repo || null}
                        onChange={(v) => setCardName(i, v)}
                        placeholder={t("scan.repo.selectPlaceholder")}
                        searchPlaceholder={t("scan.repo.searchPlaceholder")}
                        emptyText={t("scan.repo.noMatch")}
                        ungroupedLabel={t("scan.repo.ungrouped")}
                        linkedLabel={t("repos.linkedBadge")}
                      />
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label={t("scan.correlation.removeRepo")}
                      onClick={() => removeRepo(i)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    <div className="space-y-1">
                      <Label className="text-[11px] font-medium text-muted-foreground">{t("scan.correlation.roleLabel")}</Label>
                      <MiniSegmented
                        value={card.role}
                        options={[
                          { value: "entrypoint", label: t("scan.correlation.roleEntrypoint") },
                          { value: "backend", label: t("scan.correlation.roleBackend") },
                        ]}
                        onChange={(v) => setCardRole(i, v as CorrRole)}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-[11px] font-medium text-muted-foreground">{t("scan.correlation.sourceLabel")}</Label>
                      <MiniSegmented
                        value={card.reuseScanId == null ? "rescan" : "reuse"}
                        options={[
                          { value: "rescan", label: t("scan.correlation.sourceRescan") },
                          { value: "reuse", label: t("scan.correlation.sourceReuse") },
                        ]}
                        onChange={(v) => setCardSource(i, v as "rescan" | "reuse")}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-[11px] font-medium text-muted-foreground">{t("scan.correlation.protocolLabel")}</Label>
                      <Select value={card.protocol} onValueChange={(v) => setCardProtocol(i, v as CorrProtocol)}>
                        <SelectTrigger className="w-full text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {(["grpc", "http", "graphql"] as const).map((p) => (
                            <SelectItem key={p} value={p}>{p}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                  {card.reuseScanId != null && (
                    <div className="space-y-1">
                      {!card.repo.trim() ? (
                        <div className="text-xs text-muted-foreground">{t("scan.correlation.selectRepoFirst")}</div>
                      ) : candidates.length === 0 ? (
                        <div className="text-xs text-muted-foreground">{t("scan.correlation.reuseEmpty")}</div>
                      ) : (
                        <Select value={card.reuseScanId || undefined} onValueChange={(v) => setCardReuseScan(i, v)}>
                          <SelectTrigger className="w-full text-xs">
                            <SelectValue placeholder={t("scan.fields.reuseSelectPlaceholder")} />
                          </SelectTrigger>
                          <SelectContent>
                            {candidates.map((s) => (
                              <SelectItem key={s.scan_id} value={s.scan_id}>
                                <span className="font-mono text-xs">{s.workflow_id ?? s.scan_id}</span>
                                <span className="ml-1.5 text-[11px] text-muted-foreground">
                                  （{fmtTime(s.created_at)} · {String(s.status)}）
                                </span>
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            <div>
              <Button type="button" variant="outline" size="sm" onClick={addRepo}>
                {t("scan.correlation.addRepo")}
              </Button>
            </div>
          </>
        )}
      </section>

      {/* ③ relations 只读摘要（复杂拓扑在 YAML 中编辑） */}
      <section className="space-y-2">
        <SectionLabel>{t("scan.correlation.relationsTitle")}</SectionLabel>
        {state.relations.length === 0 ? (
          <div className="text-xs text-muted-foreground">{t("scan.correlation.relationsEmpty")}</div>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {state.relations.map((e, i) => (
              <span
                key={i}
                data-testid="corr-relation-chip"
                className="inline-flex items-center rounded-full border border-border bg-card px-2 py-0.5 text-[11px] font-mono"
              >
                {e.from} → {e.to} <span className="text-muted-foreground">({e.protocol})</span>
              </span>
            ))}
          </div>
        )}
        <div className="flex items-start gap-1.5 text-[11px] text-muted-foreground leading-relaxed">
          <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
          <span>{t("scan.correlation.relationsHint")}</span>
        </div>
        {/* 表单级校验（D1 validateForm；issue 文案经渲染层 i18n 映射——lib 产出中文硬编码，D7） */}
        {issues.length > 0 && (
          <div data-testid="corr-form-issues" className="space-y-0.5">
            {issues.map((m, i) => (
              <p key={i} className="text-destructive text-xs">{formatCorrIssue(m, t)}</p>
            ))}
          </div>
        )}
      </section>

      {/* ④ 黑盒验证（可选）：gateway URL + 复用共享 AuthFields/HostFields（gatewayUrl 非空时
          页面才把认证/HOST 写进提交 body——与白盒组合扫描同款 assign*ToBody）。 */}
      <section className="space-y-2.5 border-t border-border pt-4">
        <SectionLabel>{t("scan.correlation.gatewayTitle")}</SectionLabel>
        <div className="space-y-1.5">
          <Label className="text-xs font-medium">{t("scan.correlation.gatewayLabel")}</Label>
          <Input
            value={gatewayUrl}
            onChange={(e) => onGatewayUrl(e.target.value)}
            placeholder={t("scan.correlation.gatewayPlaceholder")}
            size="sm"
            className="font-mono"
          />
          {gatewayErr && <div className="text-destructive text-xs">{gatewayErr}</div>}
          <div className="text-[11px] text-muted-foreground">{t("scan.correlation.gatewayHint")}</div>
        </div>
        <AuthFields value={auth} onChange={setAuth} workspace={workspace} authErr={authErr ?? null} refreshSignal={0} />
        <HostFields value={host} onChange={setHost} workspace={workspace} error={hostErr} />
      </section>

      {/* ⑤ YAML 面板（折叠 + 显式应用） */}
      <section className="border-t border-border pt-4">
        <YamlPanel yaml={yaml} onChange={onYaml} error={yamlError} onApply={onApplyYaml} />
      </section>
    </div>
  );
}
