import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { toast } from "sonner";
import type { ScanRequest, ScanResponse, Workspace } from "../api/types";
import { apiGet, apiPost, ApiError } from "../api/client";
import { YamlEditor } from "../components/YamlEditor";
import { ScanFormFields } from "../components/ScanFormFields";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";

type ScanType = "whitebox" | "blackbox" | "correlation";

/** 黑盒「代码上下文」二选一：复用某次白盒结果 / 指定仓库代码。 */
type BlackboxReuseMode = "reuse" | "repo";

export interface FormState {
  /** 仓库代码源（白盒必选；黑盒 repo 模式必选）。入口已收窄——仅工作区已下载仓库，无本地路径。 */
  selectedRepo: string;
  url: string;
  /** 黑盒 Step3 模式开关（白盒忽略）。 */
  reuseMode: BlackboxReuseMode;
  /** reuse 模式下选中的白盒 scan_id。 */
  reuseScanId: string;
  yaml: string;
}

/**
 * 构造 /scan 提交 body。
 * P2 (2026-07-26): `workspace` 来自父组件显式选定的 workspace（替代 pre-P1 的
 * 自动生成/可选 wsName 字段）。扫描目标 ws 必须是用户可访问的已有 ws（P1 已过滤）。
 *
 * final-review C2: 字段名必须与 backend `ScanRequest` (models.py) 一致 = `workspace`。
 * pydantic v2 默认不容未知键, 发 `workspace_name` 会被静默丢弃 -> req.workspace=None -> 422
 * （P2 final-review 抓到的 prod-blocking bug: 每个前端扫描提交都 422）。
 *
 * 入口收窄（2026-07-31）：source 恒为 repo（已无本地路径）；黑盒二选一——
 *   reuse 模式发 reuse_whitebox_scan_id（不带 source），repo 模式发 source.repo。
 */
function buildBody(type: ScanType, f: FormState, workspace: string): ScanRequest {
  if (type === "correlation") return { type, config_yaml: f.yaml };
  const body: ScanRequest = { type, url: f.url || undefined, workspace: workspace || undefined };
  if (type === "whitebox") {
    body.source = { kind: "repo", value: f.selectedRepo };
    return body;
  }
  // blackbox
  if (f.reuseMode === "reuse") {
    body.reuse_whitebox_scan_id = f.reuseScanId || undefined;
  } else {
    body.source = { kind: "repo", value: f.selectedRepo };
  }
  return body;
}

function renderError(e: ApiError, t: TFunction): string {
  if (e.status === 400) return t("scan.errors.temporal");
  if (e.status === 409) return t("scan.errors.concurrent");
  if (e.status === 422) {
    const detail = (e.body as { detail?: { msg?: string }[] })?.detail;
    const msg = Array.isArray(detail) && detail.length > 0 ? detail[0]?.msg : undefined;
    return msg ? t("scan.errors.yamlInvalidWithMsg", { msg }) : t("scan.errors.yamlInvalid");
  }
  return t("scan.errors.submitFailed", { status: e.status });
}

/** 仓库校验：白盒 / 黑盒 repo 模式必选仓库。本地路径入口已移除——不再校验绝对路径。 */
function validateSource(selectedRepo: string, t: TFunction): string | null {
  return selectedRepo ? null : t("scan.errors.selectRepo");
}

function validateUrl(v: string, type: ScanType, t: TFunction): string | null {
  if (type !== "blackbox") {
    if (!v.trim()) return null;
    return /^https?:\/\//.test(v) ? null : t("scan.errors.urlScheme");
  }
  if (!v.trim()) return t("scan.errors.urlEmpty");
  return /^https?:\/\//.test(v) ? null : t("scan.errors.urlScheme");
}

export function ScanNewPage() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const presetRepo = params.get("repo");
  const presetWs = params.get("workspace");
  const [type, setType] = useState<ScanType>("whitebox");
  const [f, setF] = useState<FormState>({
    selectedRepo: "",
    url: "",
    reuseMode: "reuse",
    reuseScanId: "",
    yaml: "repos:\n  a:\n    url: https://gitlab.example/a.git\n    branch: main",
  });
  // P2: 扫描目标 ws 必须显式选定——选项来自 /workspaces（P1 后端已按当前用户可见性过滤）
  const [workspace, setWorkspace] = useState(presetWs ?? "");
  const [wsList, setWsList] = useState<Workspace[]>([]);
  // ws 列表加载中标志：初始 [] 与"加载完真的为空"都表现为 wsList=[]，需区分以防空态提示闪现
  const [wsLoading, setWsLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [yamlErr, setYamlErr] = useState("");
  const set = (patch: Partial<FormState>) => setF((prev) => ({ ...prev, ...patch }));

  useEffect(() => {
    if (presetRepo) set({ selectedRepo: presetRepo });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presetRepo]);

  // 拉取 ws 列表（用户可见的 ws，P1 后端已过滤）——供 ScanFormFields 的 ws 下拉使用
  useEffect(() => {
    apiGet<Workspace[]>("/workspaces")
      .then(setWsList)
      .catch(() => {})
      .finally(() => setWsLoading(false));
  }, []);

  const isCorrelation = type === "correlation";
  // 校验：白盒 = repo + url(可选) + ws；黑盒 = url + (reuse:scanId | repo) + ws。
  const needRepo = isCorrelation ? false : type === "whitebox" || f.reuseMode === "repo";
  const sourceErr = needRepo ? validateSource(f.selectedRepo, t) : null;
  const reuseErr = type === "blackbox" && f.reuseMode === "reuse" && !f.reuseScanId
    ? t("scan.errors.selectReuseScan")
    : null;
  const urlErr = validateUrl(f.url, type, t);
  const isValid = isCorrelation
    ? !yamlErr
    : !sourceErr && !reuseErr && !urlErr && !!workspace;

  async function onSubmit() {
    if (type === "correlation" && yamlErr) {
      toast.error(t("scan.errors.yamlRuntimeError"));
      return;
    }
    try {
      setSubmitting(true);
      const r = await apiPost<ScanResponse>("/scan", buildBody(type, f, workspace));
      // ws-scan 解耦：scan_id 有则跳 scan-scoped live（精确到刚建的 scan）；
      // 过渡期 Phase 1 未返 scan_id 时回退旧 ws-scoped live（LegacyWsTabRedirect 兜底）。
      nav(r.scan_id ? `/p/${r.workspace}/scans/${r.scan_id}/live` : `/p/${r.workspace}/live`);
    } catch (e) {
      if (e instanceof ApiError) toast.error(renderError(e, t));
    } finally {
      setSubmitting(false);
    }
  }

  const subtitleKey = type === "whitebox" ? "scan.subtitleWhitebox" : type === "blackbox" ? "scan.subtitleBlackbox" : "scan.subtitleCorrelation";
  const submitLabel = type === "blackbox" ? t("scan.submitBlackbox") : t("scan.submit");
  const footerHint = type === "blackbox" ? t("scan.footerHintBlackbox") : t("scan.footerHintWhitebox");

  return (
    <div className="space-y-4">
      {/* 页面标题 */}
      <PageHeader title={t("scan.title")} subtitle={t(subtitleKey)} />

      {/* 整张卡片：Tabs + 单栏表单 + 底部操作（侧栏已移除——信息内化进步骤） */}
      <Card className="overflow-hidden">
        {/* 自定义 Tabs 条（替换 shadcn Tabs，融入卡片） */}
        <div className="flex border-b border-border bg-secondary">
          {(["whitebox", "blackbox", "correlation"] as const).map((v) => (
            <button
              key={v}
              type="button"
              role="tab"
              aria-selected={type === v}
              onClick={() => setType(v)}
              className={`px-5 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                type === v
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {t(`scan.tabs.${v}`)}
            </button>
          ))}
        </div>

        {/* 单栏表单（correlation 铺满 yaml 编辑器；白/黑盒约束 max-w-2xl 保可读密度） */}
        <div className="p-5">
          <div className={isCorrelation ? "" : "max-w-2xl"}>
            {isCorrelation ? (
              <div className="space-y-3">
                <YamlEditor
                  value={f.yaml}
                  onChange={(v) => set({ yaml: v })}
                  onError={(m) => setYamlErr(m)}
                />
                <div className={yamlErr ? "text-sm text-destructive" : "text-xs text-muted-foreground"}>
                  {yamlErr ? t("scan.fields.yamlInvalid", { error: yamlErr }) : t("scan.fields.yamlValid")}
                </div>
              </div>
            ) : (
              <ScanFormFields
                type={type}
                f={f}
                set={set}
                sourceErr={sourceErr}
                reuseErr={reuseErr}
                urlErr={urlErr}
                workspace={workspace}
                wsList={wsList}
                onWorkspaceChange={setWorkspace}
                wsLoading={wsLoading}
              />
            )}
          </div>
        </div>

        {/* 底部操作栏 */}
        {!isCorrelation && (
          <div className="flex items-center justify-between px-5 py-3.5 border-t border-border bg-card">
            <Button variant="cta" onClick={onSubmit} disabled={!isValid || submitting}>
              {submitLabel}
            </Button>
            <span className="text-xs text-muted-foreground">{footerHint}</span>
          </div>
        )}
      </Card>

      {/* correlation 提交按钮（不在卡片底部栏内，因为无侧栏） */}
      {isCorrelation && (
        <>
          <Button variant="cta" className="w-full" onClick={onSubmit} disabled={!isValid || submitting}>
            {submitLabel}
          </Button>
          <div className="text-xs text-muted-foreground text-center">{t("scan.submitHint")}</div>
        </>
      )}
    </div>
  );
}
