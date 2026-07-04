import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { ScanRequest, ScanResponse, Workspace } from "../api/types";
import { apiGet, apiPost, ApiError } from "../api/client";
import { YamlEditor } from "../components/YamlEditor";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";

type ScanType = "whitebox" | "blackbox" | "correlation";

interface FormState {
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

  // 白盒/黑盒共享同一 form-area（仅 blackbox 多 reuse_latest，靠 type==="blackbox" 判断）。
  // Task 2 抽为内部函数避免在 TabsContent 内复制；Task 3 提升为 <ScanFormFields> 组件。
  function renderForm() {
    return (
      <div className="form-area">
        <label>
          代码来源：
          <select
            value={f.sourceKind}
            onChange={(e) => set({ sourceKind: e.target.value as "path" | "git" })}
          >
            <option value="path">本地路径</option>
            <option value="git">git URL</option>
          </select>
          <input
            value={f.sourceValue}
            onChange={(e) => set({ sourceValue: e.target.value })}
            placeholder={f.sourceKind === "path" ? "/root/code/foo" : "https://gitlab.example/foo.git"}
          />
        </label>
        {f.sourceKind === "git" && (
          <div className="git-extra">
            <input
              value={f.branch}
              onChange={(e) => set({ branch: e.target.value })}
              placeholder="分支(可选)"
            />
            <input
              value={f.commit}
              onChange={(e) => set({ commit: e.target.value })}
              placeholder="commit(可选,优先)"
            />
            <label>
              <input
                type="checkbox"
                checked={f.forceReclone}
                onChange={(e) => set({ forceReclone: e.target.checked })}
              />{" "}
              强制重新 clone
            </label>
          </div>
        )}
        <label>
          目标 URL：
          <input
            value={f.url}
            onChange={(e) => set({ url: e.target.value })}
            placeholder="http://example.com"
          />
        </label>
        <label>
          workspace 名：
          <input
            value={f.wsName}
            onChange={(e) => set({ wsName: e.target.value })}
            placeholder="空=自动 {repo}_{timestamp}"
          />
        </label>
        {type === "blackbox" && (
          <label>
            <input
              type="checkbox"
              checked={f.reuseLatest}
              onChange={(e) => set({ reuseLatest: e.target.checked })}
            />{" "}
            复用最新白盒结果{" "}
            <span className="trace">
              --latest 按 url 匹配；不勾选时后端传 --repo 显式 standalone，规避 CLI 软默认复用
            </span>
          </label>
        )}
        {conflict && (
          <div className="confirm-dialog ev-warn">
            ⚠ workspace「{conflict}」已存在，CLI -w 语义=存在则恢复，将
            <b>断点续扫</b>（恢复已有进度）。
            <button onClick={() => set({ wsName: "" })}>取消</button>
            <button className="confirm-continue" onClick={submit}>
              确认续扫
            </button>
          </div>
        )}
      </div>
    );
  }

  function renderCorrelation() {
    return (
      <div className="correlation-area">
        <YamlEditor
          value={f.yaml}
          onChange={(v) => set({ yaml: v })}
          onError={(m) => setYamlErr(m)}
        />
        <div className="trace">{yamlErr ? `⚠ ${yamlErr}` : "yaml 合法"}</div>
      </div>
    );
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
        {/* Radix Tabs 仅 mount 激活 tab 的 TabsContent；type 由 onValueChange 驱动，
            renderForm() 闭包读 type → 黑盒 tab 激活时 reuse 块自然显。 */}
        <TabsContent value="whitebox">{renderForm()}</TabsContent>
        <TabsContent value="blackbox">{renderForm()}</TabsContent>
        <TabsContent value="correlation">{renderCorrelation()}</TabsContent>
      </Tabs>

      {err && <div className="err-banner ev-error">{err}</div>}
      <button className="submit-btn" onClick={submit} disabled={!!conflict}>
        开始扫描 ▶
      </button>
      <div className="trace">→ 202 → 跳 /p/{"{ws}"}/live · 错误：400(Temporal)/409(并发)/422(yaml)</div>
    </div>
  );
}
