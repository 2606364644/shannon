import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { ScanRequest, ScanResponse, Workspace } from "../api/types";
import { apiGet, apiPost, ApiError } from "../api/client";
import { YamlEditor } from "../components/YamlEditor";
import { ScanFormFields } from "../components/ScanFormFields";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";

type ScanType = "whitebox" | "blackbox" | "correlation";

export interface FormState {
  sourceKind: "path" | "git";
  sourceValue: string;
  branch: string;
  commit: string;
  forceReclone: boolean;
  url: string;
  wsName: string;
  reuseLatest: boolean;
  yaml: string;
}

function buildBody(type: ScanType, f: FormState): ScanRequest {
  if (type === "correlation") return { type, config_yaml: f.yaml };
  const body: ScanRequest = {
    type,
    source: {
      kind: f.sourceKind,
      value: f.sourceValue,
      branch: f.branch || undefined,
      commit: f.commit || undefined,
      force_reclone: f.forceReclone || undefined,
    },
    url: f.url,
    workspace_name: f.wsName || undefined,
  };
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

export function ScanNewPage() {
  const nav = useNavigate();
  const [type, setType] = useState<ScanType>("whitebox");
  const [f, setF] = useState<FormState>({
    sourceKind: "path",
    sourceValue: "",
    branch: "",
    commit: "",
    forceReclone: false,
    url: "",
    wsName: "",
    reuseLatest: false,
    yaml: "repos:\n  a:\n    url: https://gitlab.example/a.git\n    branch: main",
  });
  const [conflict, setConflict] = useState<string | null>(null);
  const [yamlErr, setYamlErr] = useState("");
  const [err, setErr] = useState("");
  const set = (patch: Partial<FormState>) => setF((prev) => ({ ...prev, ...patch }));

  useEffect(() => {
    if (!f.wsName) {
      setConflict(null);
      return;
    }
    apiGet<Workspace[]>("/workspaces").then((ws) => {
      setConflict(ws.some((w) => w.name === f.wsName) ? f.wsName : null);
    });
  }, [f.wsName]);

  async function submit() {
    if (type === "correlation" && yamlErr) {
      setErr("yaml 有错，无法运行");
      return;
    }
    try {
      setErr("");
      const r = await apiPost<ScanResponse>("/scan", buildBody(type, f));
      nav(`/p/${r.workspace}/live`);
    } catch (e) {
      if (e instanceof ApiError) setErr(renderError(e));
    }
  }

  return (
    <div className="page scan-page">
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
            conflict={conflict}
            onConflictDismiss={() => set({ wsName: "" })}
          />
        </TabsContent>
        <TabsContent value="blackbox">
          <ScanFormFields
            type="blackbox"
            f={f}
            set={set}
            conflict={conflict}
            onConflictDismiss={() => set({ wsName: "" })}
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
              <div className="trace">{yamlErr ? `⚠ ${yamlErr}` : "yaml 合法"}</div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {err && <div className="err-banner ev-error">{err}</div>}
      <button className="submit-btn" onClick={submit} disabled={!!conflict}>
        开始扫描 ▶
      </button>
      <div className="trace">→ 202 → 跳 /p/{"{ws}"}/live · 错误：400(Temporal)/409(并发)/422(yaml)</div>
    </div>
  );
}
