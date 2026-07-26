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

export interface FormState {
  sourceKind: "repo" | "path";
  selectedRepo: string;
  sourceValue: string;
  url: string;
  reuseLatest: boolean;
  yaml: string;
}

/**
 * 构造 /scan 提交 body。
 * P2 (2026-07-26): `workspace_name` 来自父组件显式选定的 workspace（替代 pre-P1 的
 * 自动生成/可选 wsName 字段）。扫描目标 ws 必须是用户可访问的已有 ws（P1 已过滤）。
 */
function buildBody(type: ScanType, f: FormState, workspace: string): ScanRequest {
  if (type === "correlation") return { type, config_yaml: f.yaml };
  const source = f.sourceKind === "repo"
    ? { kind: "repo" as const, value: f.selectedRepo }
    : { kind: "path" as const, value: f.sourceValue };
  const body: ScanRequest = {
    type,
    source,
    url: f.url || undefined,
    workspace_name: workspace || undefined,
  };
  if (type === "blackbox") body.reuse_latest_whitebox = f.reuseLatest;
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

function validateSource(kind: "repo" | "path", selectedRepo: string, pathValue: string, t: TFunction): string | null {
  if (kind === "repo") return selectedRepo ? null : t("scan.errors.selectRepo");
  if (!pathValue.trim()) return t("scan.errors.sourceEmpty");
  return /^(\/|[A-Za-z]:[\\/])/.test(pathValue) ? null : t("scan.errors.absolutePath");
}

function validateUrl(v: string, type: ScanType, t: TFunction): string | null {
  if (type !== "blackbox") {
    if (!v.trim()) return null;
    return /^https?:\/\//.test(v) ? null : t("scan.errors.urlScheme");
  }
  if (!v.trim()) return t("scan.errors.urlEmpty");
  return /^https?:\/\//.test(v) ? null : t("scan.errors.urlScheme");
}

/** 侧栏信息卡片数据 */
interface SidebarItem {
  title: string;
  content: React.ReactNode;
}

export function ScanNewPage() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const presetRepo = params.get("repo");
  const [type, setType] = useState<ScanType>("whitebox");
  const [f, setF] = useState<FormState>({
    sourceKind: "repo",
    selectedRepo: "",
    sourceValue: "",
    url: "",
    reuseLatest: false,
    yaml: "repos:\n  a:\n    url: https://gitlab.example/a.git\n    branch: main",
  });
  // P2: 扫描目标 ws 必须显式选定——选项来自 /workspaces（P1 后端已按当前用户可见性过滤）
  const [workspace, setWorkspace] = useState("");
  const [wsList, setWsList] = useState<Workspace[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [yamlErr, setYamlErr] = useState("");
  const set = (patch: Partial<FormState>) => setF((prev) => ({ ...prev, ...patch }));

  useEffect(() => {
    if (presetRepo) set({ sourceKind: "repo", selectedRepo: presetRepo });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presetRepo]);

  // 拉取 ws 列表（用户可见的 ws，P1 后端已过滤）——供 ScanFormFields 的 ws 下拉使用
  useEffect(() => {
    apiGet<Workspace[]>("/workspaces").then(setWsList).catch(() => {});
  }, []);

  const sourceErr = validateSource(f.sourceKind, f.selectedRepo, f.sourceValue, t);
  const urlErr = validateUrl(f.url, type, t);
  const isCorrelation = type === "correlation";
  // 提交校验：source 合法 + url 合法 + workspace 已选（联动模式不需要 ws/source）
  const isValid = isCorrelation
    ? !yamlErr
    : !sourceErr && !urlErr && !!workspace;

  async function onSubmit() {
    if (type === "correlation" && yamlErr) {
      toast.error(t("scan.errors.yamlRuntimeError"));
      return;
    }
    try {
      setSubmitting(true);
      const r = await apiPost<ScanResponse>("/scan", buildBody(type, f, workspace));
      nav(`/p/${r.workspace}/live`);
    } catch (e) {
      if (e instanceof ApiError) toast.error(renderError(e, t));
    } finally {
      setSubmitting(false);
    }
  }

  // —— 侧栏内容（按扫描类型差异化） ——
  const sidebarItems: SidebarItem[] = type === "correlation" ? [] : type === "whitebox" ? [
    {
      title: t("scan.sidebar.scanType"),
      content: <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10.5px] font-semibold bg-cyan/10 text-cyan">{t("scan.tabs.whitebox")}</span>,
    },
    {
      title: t("scan.sidebar.checks"),
      content: <div className="text-xs leading-relaxed">SQL 注入 · XSS · SSRF · 认证/授权</div>,
    },
    {
      title: t("scan.sidebar.method"),
      content: <div className="text-[11.5px] leading-relaxed">📖 静态代码分析<br />🔗 数据流追踪<br />🤖 AI 辅助判定</div>,
    },
  ] : [
    {
      title: t("scan.sidebar.scanType"),
      content: <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10.5px] font-semibold bg-orange/10 text-orange">{t("scan.tabs.blackbox")}</span>,
    },
    {
      title: t("scan.sidebar.surface"),
      content: <div className="text-[11.5px] leading-relaxed">浏览器自动化探索<br />表单 / API 端点发现<br />认证绕过尝试<br />注入 / XSS / SSRF 探测</div>,
    },
    {
      title: t("scan.sidebar.method"),
      content: <div className="text-[11.5px] leading-relaxed">🌐 浏览器交互<br />🕵️ 动态探针注入<br />🤖 AI 攻击链生成</div>,
    },
  ];

  const subtitleKey = type === "whitebox" ? "scan.subtitleWhitebox" : type === "blackbox" ? "scan.subtitleBlackbox" : "scan.subtitleCorrelation";
  const submitLabel = type === "blackbox" ? t("scan.submitBlackbox") : t("scan.submit");
  const footerHint = type === "blackbox" ? t("scan.footerHintBlackbox") : t("scan.footerHintWhitebox");

  return (
    <div className="space-y-4">
      {/* 页面标题 */}
      <PageHeader title={t("scan.title")} subtitle={t(subtitleKey)} />

      {/* 整张卡片：Tabs + 双栏 + 底部操作 */}
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
              className={`px-5 py-2.5 text-[13px] font-medium border-b-2 transition-colors ${
                type === v
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {t(`scan.tabs.${v}`)}
            </button>
          ))}
        </div>

        {/* 双栏：表单 + 侧栏（correlation 除外，单栏铺满） */}
        <div className={type === "correlation" ? "" : "grid grid-cols-[1fr_260px]"}>
          {/* 左栏：表单 */}
          <div className="p-5">
            {type === "correlation" ? (
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
                urlErr={urlErr}
                workspace={workspace}
                wsList={wsList}
                onWorkspaceChange={setWorkspace}
              />
            )}
          </div>

          {/* 右栏：信息侧栏 */}
          {type !== "correlation" && (
            <div className="p-5 border-l border-border bg-card flex flex-col gap-2.5">
              <div className="text-[11px] font-semibold text-muted-foreground mb-0.5">
                {type === "whitebox" ? t("scan.sidebar.whiteboxTitle") : t("scan.sidebar.blackboxTitle")}
              </div>
              {sidebarItems.map((item, i) => (
                <div
                  key={i}
                  className="rounded-lg border border-border bg-secondary p-3"
                >
                  <div className="text-[11px] text-muted-foreground mb-1">{item.title}</div>
                  {item.content}
                </div>
              ))}
              {type === "blackbox" && (
                <div className="rounded-lg border border-orange/20 bg-orange/[0.06] p-2.5 mt-0.5">
                  <div className="text-[11px] text-orange font-medium mb-0.5">{t("scan.sidebar.blackboxWarning")}</div>
                  <div className="text-[11px] text-muted-foreground leading-relaxed">{t("scan.sidebar.blackboxWarningDesc")}</div>
                </div>
              )}
              {type === "whitebox" && (
                <div className="rounded-lg border border-yellow/15 bg-yellow/[0.06] p-2.5 mt-0.5">
                  <div className="text-[11px] text-yellow font-medium mb-0.5">{t("scan.sidebar.whiteboxHint")}</div>
                  <div className="text-[11px] text-muted-foreground leading-relaxed">{t("scan.sidebar.whiteboxHintDesc")}</div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* 底部操作栏 */}
        {type !== "correlation" && (
          <div className="flex items-center justify-between px-5 py-3.5 border-t border-border bg-card">
            <Button onClick={onSubmit} disabled={!isValid || submitting}>
              {submitLabel}
            </Button>
            <span className="text-xs text-muted-foreground">{footerHint}</span>
          </div>
        )}
      </Card>

      {/* correlation 提交按钮（不在卡片底部栏内，因为无侧栏） */}
      {type === "correlation" && (
        <>
          <Button className="w-full" onClick={onSubmit} disabled={!isValid || submitting}>
            {submitLabel}
          </Button>
          <div className="text-xs text-muted-foreground text-center">{t("scan.submitHint")}</div>
        </>
      )}
    </div>
  );
}
