// 块4: 凭据行 = 状态徽章 + 「测试登录」+ 实时过程（步骤条 + 日志）。
// 流程: 点测试登录 → testCredential 返本次 {workflow_id, probe_dir} → 挂 VerifyLivePanel 接
//   verify-events SSE（步骤条 + 实时日志）→ scan_end 时拉一次 verify-status 落终态 + refresh。
// 本次 run 的 {workflow_id, probe_dir} 只喂给实时面板（修旧 bug：原绑 credential.verify_status
// 上一次 run 旧数据）。事后回看（无 liveRun）走 verify-log review 面板。
import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { testCredential, getVerifyStatus, getVerifyLog } from "@/api/authProfiles";
import type { AuthProfile, AuthProfileCredential, VerifyState } from "@/api/types";
import { apiErrorMessage } from "@/lib/apiError";
import { VerifyLivePanel } from "./VerifyLivePanel";

interface Props {
  ws: string;
  profile: AuthProfile;
  credential: AuthProfileCredential;
  onChanged: () => void;
}

interface LiveRun {
  workflowId: string;
  probeDir: string;
  runKey: number;   // 每 test 递增 → VerifyLivePanel key 变 → 重挂载 fresh state
}

export function CredentialRow({ ws, profile, credential, onChanged }: Props) {
  const { t } = useTranslation();
  const [testing, setTesting] = useState(false);
  const [liveRun, setLiveRun] = useState<LiveRun | null>(null);
  const st: VerifyState = credential.verify_status?.state ?? "unverified";
  const vs = credential.verify_status;
  const probeDir = vs?.probe_dir;
  const workflowId = vs?.workflow_id;
  const hasLog = !!(probeDir && workflowId);
  // review 面板（事后回看）：失败默认展开；成功/unverified 折叠，手动展开。仅无 liveRun 时用。
  const [expanded, setExpanded] = useState(st === "failed");
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);

  // review 面板：展开且有 probe_dir/workflow_id 时拉 verify-log 显示 agent 登录每步。
  useEffect(() => {
    if (expanded && !liveRun && probeDir && workflowId) {
      let cancelled = false;
      getVerifyLog(ws, profile.id, credential.id, workflowId, probeDir)
        .then((r) => { if (!cancelled) setEvents(r.events); })
        .catch(() => { if (!cancelled) setEvents([]); });
      return () => { cancelled = true; };
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded, liveRun, probeDir, workflowId, ws, profile.id, credential.id]);

  const badgeCls =
    st === "success" ? "border-green/40 text-green"
    : st === "failed" ? "border-red/40 text-red"
    : "border-yellow/40 text-yellow";
  const icon = st === "success" ? "✓" : st === "failed" ? "✗" : "●";

  async function onTest() {
    setTesting(true);
    try {
      const { workflow_id, probe_dir } = await testCredential(ws, profile.id, credential.id);
      // 挂实时面板（SSE 步骤条 + 日志）；runKey 递增确保新 run fresh state。
      setLiveRun({ workflowId: workflow_id, probeDir: probe_dir, runKey: Date.now() });
    } catch (e) {
      // testCredential 本身失败（网络 / 4xx / 5xx）
      toast.error(apiErrorMessage(e, t("authProfiles.verify.failed")));
      setTesting(false);
    }
  }

  // scan_end 观测到 → 拉一次终态 verify-status（落盘 + refresh）+ toast。
  async function handleComplete(workflowId: string, probeDir: string) {
    try {
      const s = await getVerifyStatus(ws, profile.id, credential.id, workflowId, probeDir);
      if (s.state === "success") toast.success(t("authProfiles.verify.success"));
      else toast.error(t("authProfiles.verify.failed"));
    } catch (e) {
      toast.error(apiErrorMessage(e, t("authProfiles.verify.failed")));
    } finally {
      onChanged();
      setTesting(false);
    }
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <Badge variant="outline" className={`shrink-0 gap-1 font-mono ${badgeCls}`}>
          <span aria-hidden>{icon}</span>{t(`authProfiles.verify.${st}`)}
        </Badge>
        <span
          className="min-w-0 truncate font-mono text-xs"
          title={`${credential.role} · ${credential.username}`}
        >
          {credential.role} · {credential.username}
        </span>
        {!liveRun && hasLog && (
          <Button
            size="sm"
            variant="ghost"
            className="shrink-0"
            onClick={() => setExpanded((e) => !e)}
          >
            {expanded ? t("authProfiles.verify.hideProcess") : t("authProfiles.verify.viewProcess")}
          </Button>
        )}
        <Button
          size="sm"
          variant="outline"
          className="ml-auto shrink-0"
          onClick={onTest}
          disabled={testing}
          title={t("authProfiles.testHint")}
        >
          {testing
            ? <><Loader2 className="size-3 animate-spin" /> {t("authProfiles.testing")}</>
            : t("authProfiles.test")}
        </Button>
      </div>
      {st === "failed" && credential.verify_status?.failure_detail && (
        <p className="border-l-2 border-red/60 bg-red/10 px-2.5 py-1.5 text-xs leading-relaxed text-red/80">
          {credential.verify_status.failure_detail}
        </p>
      )}
      {/* 实时过程面板（本次 run 的 SSE 步骤条 + 日志）；测试中/刚测完都挂载，key=runKey 每 test fresh */}
      {liveRun && (
        <VerifyLivePanel
          key={liveRun.runKey}
          ws={ws}
          pid={profile.id}
          cid={credential.id}
          workflowId={liveRun.workflowId}
          probeDir={liveRun.probeDir}
          onComplete={handleComplete}
        />
      )}
      {/* 事后回看 review 面板（无 liveRun + 展开 + 有旧 run 记录时） */}
      {!liveRun && expanded && hasLog && (
        <div className="rounded border border-border bg-muted/30 p-2">
          <p className="mb-1 text-xs font-medium text-muted-foreground">
            {t("authProfiles.verify.process")}
          </p>
          {events.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t("authProfiles.verify.noLog")}</p>
          ) : (
            <ul className="space-y-0.5">
              {events.map((ev, i) => (
                <li key={i} className="break-all font-mono text-xs leading-relaxed">
                  {JSON.stringify(ev)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
