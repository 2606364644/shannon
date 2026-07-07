import { useEffect, useState, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import type { ScanRequest, ScanResponse, Workspace } from "../api/types";
import { apiGet, apiPost, ApiError } from "../api/client";
import { YamlEditor } from "../components/YamlEditor";
import { ScanFormFields } from "../components/ScanFormFields";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

type ScanType = "whitebox" | "blackbox" | "correlation";

export interface FormState {
  sourceKind: "repo" | "path";
  selectedRepo: string;       // repo kind 用
  sourceValue: string;        // path kind 用
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
  const body: ScanRequest = { type, source, url: f.url, workspace_name: f.wsName || undefined };
  if (type === "blackbox") body.reuse_latest_whitebox = f.reuseLatest;
  return body;
}

function renderError(e: ApiError): string {
  if (e.status === 400) return "Temporal 未就绪（localhost:7233）。先启动：docker-compose up temporal";
  if (e.status === 409) return "并发扫描超限，请等当前扫描完成或取消一个";
  if (e.status === 422) {
    // FastAPI 校验错误体：{detail:[{loc,msg,type},...]}。提取首条 msg 友好展示，
    // 不把整个 JSON 数组（含 loc/type 内部字段）丢给用户。无 detail → 回退纯标签。
    const detail = (e.body as { detail?: { msg?: string }[] })?.detail;
    const msg = Array.isArray(detail) && detail.length > 0 ? detail[0]?.msg : undefined;
    return "yaml 校验失败" + (msg ? "：" + msg : "");
  }
  return `提交失败（${e.status}）`;
}

function validateSource(kind: "repo" | "path", selectedRepo: string, pathValue: string): string | null {
  if (kind === "repo") return selectedRepo ? null : "请选择仓库";
  if (!pathValue.trim()) return "代码来源不能为空";
  return /^(\/|[A-Za-z]:[\\/])/.test(pathValue) ? null : "本地路径需为绝对路径（如 /root/code/foo）";
}

function validateUrl(v: string): string | null {
  if (!v.trim()) return "目标 URL 不能为空";
  return /^https?:\/\//.test(v) ? null : "目标 URL 需以 http(s):// 开头";
}

// 前端推算 workspace 名预览（basename + _YYYYMMDD-HHMMSS），与后端实际生成可能略有出入，
// 仅作输入辅助提示。repo→仓库名末段；path→basename（末段）。
function deriveName(kind: "repo" | "path", selectedRepo: string, pathValue: string): string {
  // selectedRepo 可为 group/repo，取末段作 ws 名 base（与后端 _gen_ws_name Path(value).stem 对齐）
  const base = kind === "repo"
    ? (selectedRepo.split("/").pop() ?? "")
    : (pathValue.trim().replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? "");
  if (!base) return "";
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const ts = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  return `${base}_${ts}`;
}

export function ScanNewPage() {
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

  // URL ?repo=<name>（来自 RepoDetailPage「发起扫描」按钮）预选仓库 + 切到 repo kind。
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

  const sourceErr = validateSource(f.sourceKind, f.selectedRepo, f.sourceValue);
  const urlErr = validateUrl(f.url);
  const isCorrelation = type === "correlation";
  const isValid =
    !sourceErr && !urlErr && !loadingConflict && !(isCorrelation && yamlErr);

  const derivedName = useMemo(
    () => (type === "correlation" ? "" : deriveName(f.sourceKind, f.selectedRepo, f.sourceValue)),
    [type, f.sourceKind, f.selectedRepo, f.sourceValue],
  );

  async function doSubmit() {
    if (type === "correlation" && yamlErr) {
      toast.error("yaml 有错，无法运行");
      return;
    }
    try {
      setSubmitting(true);
      const r = await apiPost<ScanResponse>("/scan", buildBody(type, f));
      nav(`/p/${r.workspace}/live`);
    } catch (e) {
      if (e instanceof ApiError) toast.error(renderError(e));
    } finally {
      setSubmitting(false);
    }
  }

  function onSubmit() {
    // 冲突（wsName 重名）→ 不 disable 提交，而是点提交时弹续扫确认 Dialog。
    if (conflict) {
      setConfirmOpen(true);
      return;
    }
    void doSubmit();
  }

  return (
    <div className="space-y-4">
      <Tabs
        defaultValue="whitebox"
        onValueChange={(v) => setType(v as ScanType)}
        className="w-full"
      >
        <TabsList>
          <TabsTrigger value="whitebox">白盒</TabsTrigger>
          <TabsTrigger value="blackbox">黑盒</TabsTrigger>
          <TabsTrigger value="correlation">联动</TabsTrigger>
        </TabsList>
        {/* Radix Tabs 仅 mount 激活 tab 的 TabsContent；type 由 onValueChange 驱动。
            白盒/黑盒共享 <ScanFormFields>，靠 type prop 决定 reuse 块是否渲染。 */}
        <TabsContent value="whitebox">
          <ScanFormFields
            type="whitebox"
            f={f}
            set={set}
            sourceErr={sourceErr}
            urlErr={urlErr}
            loadingConflict={loadingConflict}
            derivedName={derivedName}
          />
        </TabsContent>
        <TabsContent value="blackbox">
          <ScanFormFields
            type="blackbox"
            f={f}
            set={set}
            sourceErr={sourceErr}
            urlErr={urlErr}
            loadingConflict={loadingConflict}
            derivedName={derivedName}
          />
        </TabsContent>
        <TabsContent value="correlation">
          <Card>
            <CardHeader><CardTitle>联动扫描</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <YamlEditor
                value={f.yaml}
                onChange={(v) => set({ yaml: v })}
                onError={(m) => setYamlErr(m)}
              />
              <div className={yamlErr ? "text-sm text-destructive" : "text-xs text-muted-foreground"}>
                {yamlErr ? `⚠ ${yamlErr}` : "yaml 合法"}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <Button size="lg" className="w-full" onClick={onSubmit} disabled={!isValid || submitting}>
        开始扫描 ▶
      </Button>
      <div className="text-xs text-muted-foreground">→ 202 → 跳 /p/{"{ws}"}/live · 错误：400(Temporal)/409(并发)/422(yaml)</div>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent onInteractOutside={(e) => e.preventDefault()}>
          <DialogHeader>
            <DialogTitle>断点续扫确认</DialogTitle>
            <DialogDescription>
              workspace「{conflict ?? ""}」已存在。CLI -w 语义=存在则恢复，将断点续扫（恢复已有进度）。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => { set({ wsName: "" }); setConfirmOpen(false); }}>
              取消（清空名）
            </Button>
            <Button onClick={() => { setConfirmOpen(false); void doSubmit(); }}>
              确认续扫
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
