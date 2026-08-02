import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { toast } from "sonner";
import type { ScanRequest, ScanResponse, Workspace, ScanAuthentication } from "../api/types";
import { apiGet, apiPost, ApiError } from "../api/client";
import { YamlEditor } from "../components/YamlEditor";
import { ScanFormFields } from "../components/ScanFormFields";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";

type ScanType = "whitebox" | "blackbox" | "correlation";

export type LoginType = "form" | "sso" | "api" | "basic";
export type SuccessConditionType = "url_contains" | "element_present" | "url_equals_exactly" | "text_contains";

/** 黑盒登录配置表单态（独立于 ScanAuthentication 契约：含 enabled 开关 + emailLoginEnabled
 *  + loginFlow 多行文本；buildBody 时 buildAuthPayload 转成 ScanAuthentication）。 */
export interface AuthFormState {
  enabled: boolean;
  loginType: LoginType;
  loginUrl: string;
  username: string;
  password: string;
  totpSecret: string;
  emailLoginEnabled: boolean;
  emailAddress: string;
  emailPassword: string;
  emailTotp: string;
  loginFlow: string; // textarea 多行；buildBody 时 split 成 string[]
  scType: SuccessConditionType;
  scValue: string;
}

const DEFAULT_AUTH: AuthFormState = {
  enabled: false,
  loginType: "form",
  loginUrl: "",
  username: "",
  password: "",
  totpSecret: "",
  emailLoginEnabled: false,
  emailAddress: "",
  emailPassword: "",
  emailTotp: "",
  loginFlow: "",
  scType: "url_contains",
  scValue: "",
};

/** AuthFormState → ScanAuthentication（对齐后端 core Authentication schema，snake_case 字段名）。 */
export function buildAuthPayload(a: AuthFormState): ScanAuthentication {
  const credentials: ScanAuthentication["credentials"] = { username: a.username.trim() };
  if (a.password) credentials.password = a.password;
  if (a.totpSecret.trim()) credentials.totp_secret = a.totpSecret.trim();
  if (a.emailLoginEnabled) {
    credentials.email_login = { address: a.emailAddress.trim(), password: a.emailPassword };
    if (a.emailTotp.trim()) credentials.email_login.totp_secret = a.emailTotp.trim();
  }
  const payload: ScanAuthentication = {
    login_type: a.loginType,
    login_url: a.loginUrl.trim(),
    credentials,
    success_condition: { type: a.scType, value: a.scValue.trim() },
  };
  const flow = a.loginFlow.split("\n").map((s) => s.trim()).filter(Boolean);
  if (flow.length) payload.login_flow = flow;
  return payload;
}

export function validateAuth(a: AuthFormState, t: TFunction): string | null {
  if (!a.enabled) return null;
  if (!a.loginUrl.trim()) return t("scan.errors.authLoginUrlEmpty");
  if (!/^https?:\/\//.test(a.loginUrl.trim())) return t("scan.errors.authLoginUrl");
  if (!a.username.trim()) return t("scan.errors.authUsername");
  if (!a.scValue.trim()) return t("scan.errors.authScValue");
  return null;
}

export interface FormState {
  /** 仓库代码源（白盒必选）。入口已收窄——仅工作区已下载仓库，无本地路径。 */
  selectedRepo: string;
  url: string;
  /** 黑盒必填：要复用的白盒 scan_id。黑盒恒复用白盒结果（exploitation-only），无独立 repo 模式。 */
  reuseScanId: string;
  /** 黑盒登录配置（仅 blackbox 用；whitebox/correlation 忽略）。 */
  auth: AuthFormState;
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
 * 入口收窄（2026-08-01，阶段 2）：黑盒 = 白盒下游 exploitation-only，恒复用白盒结果——
 *   恒发 reuse_whitebox_scan_id（必填，前端校验拦空），不再有 repo/standalone 分支。source 仅白盒用。
 */
function buildBody(type: ScanType, f: FormState, workspace: string): ScanRequest {
  if (type === "correlation") return { type, config_yaml: f.yaml };
  const body: ScanRequest = { type, url: f.url || undefined, workspace: workspace || undefined };
  if (type === "whitebox") {
    body.source = { kind: "repo", value: f.selectedRepo };
    return body;
  }
  // blackbox：恒复用白盒结果（exploitation-only）。reuseScanId 由前端校验保证非空（提交按钮 disabled）。
  body.reuse_whitebox_scan_id = f.reuseScanId || undefined;
  if (f.auth.enabled) {
    body.authentication = buildAuthPayload(f.auth);
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
    reuseScanId: "",
    auth: DEFAULT_AUTH,
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
  // 校验：白盒 = repo + url(可选) + ws；黑盒 = url + reuseScanId(必填) + ws（恒复用白盒，无 repo 模式）。
  const needRepo = isCorrelation ? false : type === "whitebox";
  const sourceErr = needRepo ? validateSource(f.selectedRepo, t) : null;
  const reuseErr = type === "blackbox" && !f.reuseScanId
    ? t("scan.errors.selectReuseScan")
    : null;
  const urlErr = validateUrl(f.url, type, t);
  const authErr = type === "blackbox" ? validateAuth(f.auth, t) : null;
  const isValid = isCorrelation
    ? !yamlErr
    : !sourceErr && !reuseErr && !urlErr && !authErr && !!workspace;

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
                authErr={authErr}
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
