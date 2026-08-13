import { useEffect, useState } from "react";
import { useNavigate, useSearchParams, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { toast } from "sonner";
import type { ScanRequest, ScanResponse, Workspace, ScanAuthentication } from "../api/types";
import { apiGet, apiPost, ApiError } from "../api/client";
import { YamlEditor } from "../components/YamlEditor";
import { ScanFormFields } from "../components/ScanFormFields";
import type { CredentialDraft } from "../components/auth/CredentialRows";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";

type ScanType = "whitebox" | "blackbox" | "correlation";

export type LoginType = "form" | "sso" | "api" | "basic";

/** 黑盒登录配置表单态（独立于 ScanAuthentication 契约：含 enabled 开关 + emailLoginEnabled
 *  + loginFlow 多行文本；buildBody 时 buildAuthPayload 转成 ScanAuthentication）。
 *
 *  auth-profile-vault（Task 14）：enabled=true 时按 `source` 切换两条来源——
 *    - "inline"：临时手填（即下方 loginType/loginUrl/credentials/loginFlow），buildBody 发 `authentication`。
 *    - "profile"：复用工作区已验证档案（profileId + credentialIds 指向 profile.credentials[] 哪些角色），
 *      buildBody 发 `auth_profile_id` + `auth_credential_ids`（与 `authentication` 互斥，后端 Task 7 XOR 校验）。
 *  enabled=false 时 source 无意义（关闭即不登录）。 */
export interface AuthFormState {
  enabled: boolean;
  /** enabled=true 时的来源分支（disabled 模式下无意义，默认 inline 保留兼容）。 */
  source: "inline" | "profile";
  /** profile 模式：选定的认证档案 id（GET /workspaces/{ws}/auth-profiles 列表中一项）。 */
  profileId: string;
  /** profile 模式：选定档案下哪些角色凭据 id（profile.credentials[] 子集；空=后端全选，前端默认全选）。 */
  credentialIds: string[];
  loginType: LoginType;
  loginUrl: string;
  /** inline 凭据（统一多角色）：accounts[0]=primary（不可删，默认 role="admin"）→ buildAuthPayload 发
   *  authentication.credentials；accounts.slice(1)=附加角色 → buildBody 发 auth_accounts（多身份对比）。 */
  accounts: CredentialDraft[];
  loginFlow: string; // textarea 多行；buildBody 时 split 成 string[]
}

const DEFAULT_AUTH: AuthFormState = {
  enabled: false,
  source: "inline",
  profileId: "",
  credentialIds: [],
  loginType: "form",
  loginUrl: "",
  accounts: [{ role: "admin", username: "", password: "" }],
  loginFlow: "",
};

/** HOST 解析表单态（blackbox-host-profile, Task 13）。镜像 AuthFormState 的双来源结构：
 *    - enabled=false：不起代理（向后兼容，直连目标）。
 *    - mode="profile"：复用工作区 HOST 档案（host_profile_id，Task 12 已落 backend）。
 *    - mode="url"：临时填 /etc/hosts 风格文本 URL（host_url，后端拉取解析为 mappings）。
 *  与 auth 独立（非互斥）——二者各管各的：auth 管登录态，host 管 DNS 覆盖。 */
export interface HostFormState {
  enabled: boolean;
  mode: "profile" | "url";
  /** profile 模式：选定的 HOST 档案 id（GET /workspaces/{ws}/host-profiles 列表中一项）。 */
  profileId: string;
  /** url 模式：/etc/hosts 风格文本 URL（后端 POST /parse?url=<URL> 拉取解析，不落盘）。 */
  hostUrl: string;
}

const DEFAULT_HOST: HostFormState = {
  enabled: false,
  mode: "profile",
  profileId: "",
  hostUrl: "",
};

/** AuthFormState → ScanAuthentication（对齐后端 core Authentication schema，snake_case 字段名）。
 *  微调（2026-08-06）：inline 模式不再采集 email_login（删邮箱登录框）；role 仅用于存档不发。 */
export function buildAuthPayload(a: AuthFormState): ScanAuthentication {
  const primary = a.accounts[0];
  const credentials: ScanAuthentication["credentials"] = { username: (primary?.username ?? "").trim() };
  if (primary?.password) credentials.password = primary.password;
  const payload: ScanAuthentication = {
    login_type: a.loginType,
    login_url: a.loginUrl.trim(),
    credentials,
  };
  const flow = a.loginFlow.split("\n").map((s) => s.trim()).filter(Boolean);
  if (flow.length) payload.login_flow = flow;
  return payload;
}

/** ScanAuthentication -> AuthFormState（buildAuthPayload 的逆映射，供重跑预填黑盒登录配置）。
 *  始终返回 inline 模式（auth-profile-vault Task 14：profile 模式由 presetToAuthState 单独判定）。 */
export function authFromPayload(auth: ScanAuthentication): AuthFormState {
  const c = auth.credentials ?? { username: "" };
  return {
    enabled: true,
    source: "inline",
    profileId: "",
    credentialIds: [],
    loginType: auth.login_type ?? "form",
    loginUrl: auth.login_url ?? "",
    accounts: [{ role: "admin", username: c.username ?? "", password: c.password ?? "" }],
    loginFlow: Array.isArray(auth.login_flow) ? auth.login_flow.join("\n") : "",
  };
}

/** 重跑预填数据（ScanList.onRerun 经 location.state 传入，优先于 query param）。 */
export interface RerunPreset {
  type?: ScanType;
  workspace?: string;
  repo?: string;
  url?: string;
  reuseScanId?: string;
  /** inline 模式登录配置（旧字段，与 authProfileId 互斥）。 */
  auth?: ScanAuthentication;
  /** profile 模式（auth-profile-vault Task 14）：原扫描使用了某条认证档案+角色，
   *  重跑时预填到 source=profile 分支。后端 _scan_detail 暂未返此字段（前端先就位）。 */
  authProfileId?: string;
  authCredentialIds?: string[];
  /** HOST 解析（Task 13）：原扫描启用了 HOST 解析，重跑时预填。
   *  hostProfileId 非空 → profile 模式；仅 hostUrl → url 模式；后端 _scan_detail 暂未返（前端先就位）。 */
  hostProfileId?: string;
  hostUrl?: string;
}

/** RerunPreset → AuthFormState：profile 模式（authProfileId 非空）优先于 inline（auth）。
 *  与 buildBody 一致：profile 模式发 auth_profile_id + auth_credential_ids（空数组=后端全选），
 *  inline 模式发 authentication。 */
export function presetToAuthState(preset: RerunPreset): AuthFormState {
  if (preset.authProfileId) {
    return {
      ...DEFAULT_AUTH,
      enabled: true,
      source: "profile",
      profileId: preset.authProfileId,
      credentialIds: preset.authCredentialIds ?? [],
    };
  }
  if (preset.auth) return authFromPayload(preset.auth);
  return DEFAULT_AUTH;
}

/** RerunPreset → HostFormState：profile 模式（hostProfileId 非空）优先于 url 模式（hostUrl）。
 *  与 buildBody 一致：profile 模式发 host_profile_id，url 模式发 host_url。 */
export function presetToHostState(preset: RerunPreset): HostFormState {
  if (preset.hostProfileId) {
    return { ...DEFAULT_HOST, enabled: true, mode: "profile", profileId: preset.hostProfileId };
  }
  if (preset.hostUrl) {
    return { ...DEFAULT_HOST, enabled: true, mode: "url", hostUrl: preset.hostUrl };
  }
  return DEFAULT_HOST;
}

function hostValidationKey(h: HostFormState): string | null {
  if (!h.enabled) return null;
  if (h.mode === "profile") {
    return h.profileId.trim() ? null : "scan.errors.hostProfileRequired";
  }
  const url = h.hostUrl.trim();
  if (!url) return "scan.errors.hostUrlRequired";
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? null
      : "scan.errors.hostUrlScheme";
  } catch {
    return "scan.errors.hostUrlScheme";
  }
}

/** HOST enabled state must always have a concrete, safe source. */
export function validateHost(h: HostFormState, t: TFunction): string | null {
  const key = hostValidationKey(h);
  return key ? t(key) : null;
}

export function validateAuth(a: AuthFormState, t: TFunction): string | null {
  if (!a.enabled) return null;
  // profile 模式：必须选定档案 + 至少一个角色（前端默认全选；取消全选则拦空）。
  // 错误文案用 scan.errors.auth{Profile,Credential}Required（与 ProfilePicker 的
  // SelectValue placeholder「选择认证档案/选择登录角色」不同文本，避免 getByText 多义）。
  if (a.source === "profile") {
    if (!a.profileId) return t("scan.errors.authProfileRequired");
    if (!a.credentialIds.length) return t("scan.errors.authCredentialRequired");
    return null;
  }
  if (!a.loginUrl.trim()) return t("scan.errors.authLoginUrlEmpty");
  if (!/^https?:\/\//.test(a.loginUrl.trim())) return t("scan.errors.authLoginUrl");
  // primary（accounts[0]）须用户名；附加角色（slice(1)）每条须用户名 + 密码（可登录），否则拦空。
  const primary = a.accounts[0];
  if (!primary || !primary.username.trim()) return t("scan.errors.authUsername");
  for (const acc of a.accounts.slice(1)) {
    if (!acc.username.trim() || !acc.password) return t("scan.errors.authAccountIncomplete");
  }
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
  /** HOST 解析（仅 blackbox 用；与 auth 独立，非互斥）。disabled=不起代理，向后兼容。 */
  host: HostFormState;
  yaml: string;
  /** 白盒「同时发起黑盒扫描」组合开关（Task 9）。可选——旧 FormState 字面量（如单测 baseF）不传 = false。
   *  true 时 buildBody whitebox 分支附 url + 认证字段，后端 Task 1 识别为组合扫描。 */
  combined?: boolean;
}

/** 将 AuthFormState 写入 ScanRequest 认证字段（auth-profile-vault 双来源）：
 *    - inline 模式 → authentication（+附加角色 auth_accounts）。
 *    - profile 模式 → auth_profile_id + auth_credential_ids（空数组=后端全选）。
 *  黑盒与白盒组合扫描共用（Task 9），保证两条分支字段映射一致。 */
function assignAuthToBody(body: ScanRequest, a: AuthFormState): void {
  if (a.source === "profile") {
    body.auth_profile_id = a.profileId || undefined;
    body.auth_credential_ids = a.credentialIds.length ? a.credentialIds : undefined;
  } else {
    body.authentication = buildAuthPayload(a);
    // 附加角色（accounts.slice(1)）→ auth_accounts（后端 scan_manager 展开成 accounts[]）。
    // guard 用 slice(1) 后长度：accounts[0] 恒为 primary，若用 accounts.length 会发空 auth_accounts:[]。
    const extra = a.accounts.slice(1);
    if (extra.length) {
      body.auth_accounts = extra.map((acc) => ({
        role: acc.role.trim() || "role",
        username: acc.username.trim(),
        password: acc.password,
      }));
    }
  }
}

/** 将 HostFormState 写入 ScanRequest 的 HOST 字段（与 assignAuthToBody 同款共享）：
 *    - profile 模式 -> host_profile_id；url 模式 -> host_url。
 *    - enabled 时发；disabled 不发（向后兼容——不起代理，直连目标）。
 *    - 空值兜底 || undefined（不发空串）。
 *  黑盒与白盒组合扫描共用，保证两条分支字段映射一致。HOST 与认证独立、非互斥。
 *  仅组合模式（combined && url）/黑盒调——纯白盒（无 url）不发 host（无黑盒阶段，HOST 代理无意义）。 */
function assignHostToBody(body: ScanRequest, h: HostFormState): void {
  if (!h.enabled) return;
  if (h.mode === "profile") body.host_profile_id = h.profileId || undefined;
  else body.host_url = h.hostUrl || undefined;
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
export function buildBody(type: ScanType, f: FormState, workspace: string): ScanRequest {
  if (type === "correlation") return { type, config_yaml: f.yaml };
  const hostIsActive = type === "blackbox" || (type === "whitebox" && !!f.combined && !!f.url);
  if (hostIsActive) {
    const hostError = hostValidationKey(f.host);
    if (hostError) throw new Error(hostError);
  }
  const body: ScanRequest = { type, url: f.url || undefined, workspace: workspace || undefined };
  if (type === "whitebox") {
    body.source = { kind: "repo", value: f.selectedRepo };
    // 组合扫描（Task 9）：开关开 + url → 同一 body 携带 url + 认证，后端 Task 1 validator
    // 识别 type=whitebox + url → 组合扫描，先跑黑盒认证预验证（bb_phase=precheck）。
    // 开关关 → 纯白盒：即便 f.url 有草稿也不发（strip 上方 line 设的 url），零回归。
    if (f.combined && f.url) {
      body.url = f.url;
      if (f.auth.enabled) assignAuthToBody(body, f.auth);
      assignHostToBody(body, f.host);
    } else {
      body.url = undefined;
    }
    return body;
  }
  // blackbox：恒复用白盒结果（exploitation-only）。reuseScanId 由前端校验保证非空（提交按钮 disabled）。
  body.reuse_whitebox_scan_id = f.reuseScanId || undefined;
  // 认证（auth-profile-vault Task 14 双来源）：与白盒组合扫描共用 assignAuthToBody（字段映射一致）。
  if (f.auth.enabled) assignAuthToBody(body, f.auth);
  // HOST 解析（blackbox-host-profile Task 13）：enabled 时按 mode 发对应字段（与 auth 独立、非互斥）。
  // profile 模式 → host_profile_id；url 模式 → host_url。空值兜底 || undefined（不发空串）。
  // disabled 时两者都不发（向后兼容——不起代理，直连目标）。
  assignHostToBody(body, f.host);
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
  // 重跑预填：ScanList.onRerun 经 location.state 传入原扫描配置（优先于 query param）。
  const preset = (useLocation().state ?? {}) as RerunPreset;
  const [type, setType] = useState<ScanType>(preset.type ?? "whitebox");
  const [f, setF] = useState<FormState>({
    selectedRepo: preset.repo ?? presetRepo ?? "",
    url: preset.url ?? "",
    reuseScanId: preset.reuseScanId ?? "",
    auth: presetToAuthState(preset),
    host: presetToHostState(preset),
    yaml: "repos:\n  a:\n    url: https://gitlab.example/a.git\n    branch: main",
    combined: false,
  });
  // P2: 扫描目标 ws 必须显式选定——选项来自 /workspaces（P1 后端已按当前用户可见性过滤）
  const [workspace, setWorkspace] = useState(preset.workspace ?? presetWs ?? "");
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
  // 白盒组合扫描（Task 9）：combined 开时 url 变必填 + auth 纳入校验（与黑盒同款 validateAuth）。
  const needRepo = isCorrelation ? false : type === "whitebox";
  const sourceErr = needRepo ? validateSource(f.selectedRepo, t) : null;
  const reuseErr = type === "blackbox" && !f.reuseScanId
    ? t("scan.errors.selectReuseScan")
    : null;
  const combined = type === "whitebox" && !!f.combined;
  const urlErr = combined
    ? (f.url.trim() ? (/^https?:\/\//.test(f.url.trim()) ? null : t("scan.errors.urlScheme")) : t("scan.errors.urlEmpty"))
    : validateUrl(f.url, type, t);
  const authErr = (type === "blackbox" || combined) ? validateAuth(f.auth, t) : null;
  const hostErr = (type === "blackbox" || combined) ? validateHost(f.host, t) : null;
  const isValid = isCorrelation
    ? !yamlErr
    : !sourceErr && !reuseErr && !urlErr && !authErr && !hostErr && !!workspace;

  async function onSubmit() {
    if (type === "correlation" && yamlErr) {
      toast.error(t("scan.errors.yamlRuntimeError"));
      return;
    }
    try {
      setSubmitting(true);
      const r = await apiPost<ScanResponse>("/scan", buildBody(type, f, workspace));
      // 组合扫描预验证态（Task 9，spec §8.2）：后端 passthrough bb_phase=precheck 表示
      // 黑盒认证预验证先行——提示用户「预验证中」，再跳 live 页跟踪进度。
      if (r.bb_phase === "precheck") toast.info(t("scan.precheckStatus"));
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

        {/* 表单区：白盒/黑盒均满宽卡（白盒旧 max-w-2xl 左贴致满宽卡右半空洞，已移除——
            白盒改由 ScanFormFields 内 lg:grid-cols-2 把 ① 工作区 / ② 代码源 并排铺满，③ 满宽）。 */}
        <div className="p-5">
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
              hostErr={hostErr}
              workspace={workspace}
              wsList={wsList}
              onWorkspaceChange={setWorkspace}
              wsLoading={wsLoading}
              presetReuseScanId={preset.reuseScanId}
            />
          )}
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
