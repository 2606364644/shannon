import { useEffect, useState, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { toast } from "sonner";
import type { ScanRequest, ScanResponse, Workspace } from "../api/types";
import { apiGet, apiPost, ApiError } from "../api/client";
import { YamlEditor } from "../components/YamlEditor";
import { ScanFormFields } from "../components/ScanFormFields";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

type ScanType = "whitebox" | "blackbox" | "correlation";

export interface FormState {
  sourceKind: "repo" | "path";
  selectedRepo: string;
  sourceValue: string;
  url: string;
  wsName: string;
  reuseLatest: boolean;
  yaml: string;
}

function buildBody(type: ScanType, f: FormState): ScanRequest {
  if (type === "correlation") return { type, config_yaml: f.yaml };
  const source = f.sourceKind === "repo"
    ? { kind: "repo" as const, value: f.selectedRepo }
    : { kind: "path" as const, value: f.sourceValue };
  const body: ScanRequest = { type, source, url: f.url || undefined, workspace_name: f.wsName || undefined };
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

function deriveName(kind: "repo" | "path", selectedRepo: string, pathValue: string): string {
  const base = kind === "repo"
    ? (selectedRepo.split("/").pop() ?? "")
    : (pathValue.trim().replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? "");
  if (!base) return "";
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const ts = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  return `${base}_${ts}`;
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
    wsName: "",
    reuseLatest: false,
    yaml: "repos:\n  a:\n    url: https://gitlab.example/a.git\n    branch: main",
  });
  const [conflict, setConflict] = useState<string | null>(null);
  const [loadingConflict, setLoadingConflict] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [yamlErr, setYamlErr] = useState("");
  const set = (patch: Partial<FormState>) => setF((prev) => ({ ...prev, ...patch }));

  useEffect(() => {
    if (presetRepo) set({ sourceKind: "repo", selectedRepo: presetRepo });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presetRepo]);

  useEffect(() => {
    if (!f.wsName) {
      setConflict(null);
      setLoadingConflict(false);
      return;
    }
    setLoadingConflict(true);
    const t = setTimeout(() => {
      apiGet<Workspace[]>("/workspaces")
        .then((ws) => {
          setConflict(ws.some((w) => w.name === f.wsName) ? f.wsName : null);
        })
        .finally(() => setLoadingConflict(false));
    }, 300);
    return () => clearTimeout(t);
  }, [f.wsName]);

  const sourceErr = validateSource(f.sourceKind, f.selectedRepo, f.sourceValue, t);
  const urlErr = validateUrl(f.url, type, t);
  const isCorrelation = type === "correlation";
  const isValid =
    !sourceErr && !urlErr && !loadingConflict && !(isCorrelation && yamlErr);

  const derivedName = useMemo(
    () => (type === "correlation" ? "" : deriveName(f.sourceKind, f.selectedRepo, f.sourceValue)),
    [type, f.sourceKind, f.selectedRepo, f.sourceValue],
  );

  async function doSubmit() {
    if (type === "correlation" && yamlErr) {
      toast.error(t("scan.errors.yamlRuntimeError"));
      return;
    }
    try {
      setSubmitting(true);
      const r = await apiPost<ScanResponse>("/scan", buildBody(type, f));
      nav(`/p/${r.workspace}/live`);
    } catch (e) {
      if (e instanceof ApiError) toast.error(renderError(e, t));
    } finally {
      setSubmitting(false);
    }
  }

  function onSubmit() {
    if (conflict) {
      setConfirmOpen(true);
      return;
    }
    void doSubmit();
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
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{t("scan.title")}</h1>
        <p className="text-sm text-muted-foreground mt-0.5">{t(subtitleKey)}</p>
      </div>

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
                loadingConflict={loadingConflict}
                derivedName={derivedName}
              />
            )}
          </div>

          {/* 右栏：信息侧栏 */}
          {type !== "correlation" && (
            <div className="p-5 border-l border-border bg-card flex flex-col gap-2.5">
              <div className="text-[11px] font-semibold text-primary uppercase tracking-wider mb-0.5">
                {type === "whitebox" ? t("scan.sidebar.whiteboxTitle") : t("scan.sidebar.blackboxTitle")}
              </div>
              {sidebarItems.map((item, i) => (
                <div
                  key={i}
                  className="rounded-lg border border-primary/25 bg-primary/[0.06] p-3"
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
            <span className="text-xs text-muted-foreground">{footerHint}</span>
            <Button size="lg" onClick={onSubmit} disabled={!isValid || submitting}>
              {submitLabel}
            </Button>
          </div>
        )}
      </Card>

      {/* correlation 提交按钮（不在卡片底部栏内，因为无侧栏） */}
      {type === "correlation" && (
        <>
          <Button size="lg" className="w-full" onClick={onSubmit} disabled={!isValid || submitting}>
            {submitLabel}
          </Button>
          <div className="text-xs text-muted-foreground text-center">{t("scan.submitHint")}</div>
        </>
      )}

      {/* 续扫确认 Dialog */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent onInteractOutside={(e) => e.preventDefault()}>
          <DialogHeader>
            <DialogTitle>{t("scan.resume.title")}</DialogTitle>
            <DialogDescription>
              {t("scan.resume.desc", { name: conflict ?? "" })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => { set({ wsName: "" }); setConfirmOpen(false); }}>
              {t("scan.resume.cancel")}
            </Button>
            <Button onClick={() => { setConfirmOpen(false); void doSubmit(); }}>
              {t("scan.resume.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
