// 认证过程页（新标签页打开: /p/:ws/auth-profiles/:pid/credentials/:cid）。
// 列表 chip 点击 → window.open 此页。把认证过程从列表内嵌（旧 CredentialRow, 步骤条+日志直接长在行里）
// 独立成专页——列表保持轻量紧凑(不换行), 过程在此专注展示 + 持久化回看(最近一次 run)。
// 承载: ① 测试登录(testCredential → VerifyLivePanel SSE 实时) ② 回看(getVerifyLog → DashboardPanel + LogStream)。
import { useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ArrowLeft, Loader2 } from "lucide-react";
import { getAuthProfile, testCredential, getVerifyStatus, getVerifyLog } from "@/api/authProfiles";
import { apiErrorMessage } from "@/lib/apiError";
import type { AuthProfileCredential, NdjsonEvent, VerifyState } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { VerifyLivePanel } from "./VerifyLivePanel";
import { VerifyFailureNote } from "@/components/auth/VerifyFailureNote";
import { DashboardPanel } from "@/components/DashboardPanel";
import { LogStream } from "@/components/LogStream";
import { dashboardReducer, emptyState } from "@/state/dashboardReducer";
import type { DashboardState } from "@/state/dashboardReducer";

interface LiveRun { workflowId: string; probeDir: string; runKey: number; }

// 状态色（对齐旧 CredentialRow: success绿 / failed红 / unverified黄 / running蓝）
function statusVisual(st: VerifyState) {
  if (st === "success") return { icon: "✓", cls: "border-green/40 text-green" };
  if (st === "failed") return { icon: "✗", cls: "border-red/40 text-red" };
  if (st === "running") return { icon: "●", cls: "border-blue/40 text-blue" };
  return { icon: "●", cls: "border-yellow/40 text-yellow" };
}

function fmtTime(iso?: string) {
  if (!iso) return null;
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

export function VerifyProcessPage() {
  const { t } = useTranslation();
  const { workspace, pid, cid } = useParams<{ workspace: string; pid: string; cid: string }>();
  const [profileName, setProfileName] = useState<string>("");
  const [profileLoginUrl, setProfileLoginUrl] = useState<string>("");
  const [credential, setCredential] = useState<AuthProfileCredential | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [liveRun, setLiveRun] = useState<LiveRun | null>(null);
  // 恢复态守卫：记已恢复过的 workflowId，防 refreshTick 重拉 profile 致 VerifyLivePanel 反复重挂。
  const recoveredWfRef = useRef<string | null>(null);
  const [replay, setReplay] = useState<NdjsonEvent[]>([]);
  const [refreshTick, setRefreshTick] = useState(0);   // 测试完成后 +1 → 重拉 profile 落最新 verify_status

  // 拉 profile（refreshTick 变 → 重拉）
  useEffect(() => {
    if (!workspace || !pid || !cid) return;
    let cancelled = false;
    setLoading(true); setError(null);
    getAuthProfile(workspace, pid)
      .then((p) => {
        if (cancelled) return;
        setProfileName(p.name);
        setProfileLoginUrl(p.login_url);
        setCredential(p.credentials.find((c) => c.id === cid) ?? null);
        setLoading(false);
      })
      .catch((e) => { if (!cancelled) { setError(apiErrorMessage(e, t("authProfiles.loadFailed"))); setLoading(false); } });
    return () => { cancelled = true; };
  }, [workspace, pid, cid, refreshTick]);  // eslint-disable-line react-hooks/exhaustive-deps

  const vs = credential?.verify_status;
  const st: VerifyState = vs?.state ?? "unverified";
  const sv = statusVisual(st);
  const wfId = vs?.workflow_id;
  const pd = vs?.probe_dir;
  const hasHistory = !!(wfId && pd);

  // 回看: 无 liveRun + 有历史 run → 拉 verify-log 持久化日志（最近一次完整过程）
  useEffect(() => {
    if (!workspace || !pid || !cid || liveRun || !wfId || !pd) { setReplay([]); return; }
    let cancelled = false;
    getVerifyLog(workspace, pid, cid, wfId, pd)
      .then((r) => { if (!cancelled) setReplay((r.events as NdjsonEvent[]) ?? []); })
      .catch(() => { if (!cancelled) setReplay([]); });
    return () => { cancelled = true; };
  }, [workspace, pid, cid, liveRun, wfId, pd]);

  // 恢复：profile 显示 state=running 且有 wfId/pd，但 liveRun 未设（组件重挂场景——用户离开过程页
  // 再回来）→ 重挂 VerifyLivePanel 重连 SSE。EventTailer 从头重放 events.ndjson 追上现实进度。
  // runKey 用稳定常数 0（非 Date.now()）：profile 会被 refreshTick 重拉，runKey 每次变会致
  // EventSource flapping。recoveredWfRef 守卫同 wf 只恢复一次；liveRun 守卫不覆盖主动测的 run。
  useEffect(() => {
    if (liveRun) return;
    if (st !== "running" || !wfId || !pd) return;
    if (recoveredWfRef.current === wfId) return;
    recoveredWfRef.current = wfId;
    setLiveRun({ workflowId: wfId, probeDir: pd, runKey: 0 });
    setTesting(true);
  }, [st, wfId, pd, liveRun]);

  async function onTest() {
    if (!workspace || !pid || !cid) return;
    // 进行中守卫：防恢复态极短窗口（loading 结束、恢复 effect 未跑）重复触发 start_auth_validation，
    // 其覆盖清理会 rmtree 正在跑的 probe 目录（scan_manager.start_auth_validation:540-545）。
    if (st === "running") { toast.info(t("authProfiles.verify.alreadyRunning")); return; }
    setTesting(true);
    try {
      const { workflow_id, probe_dir } = await testCredential(workspace, pid, cid);
      setLiveRun({ workflowId: workflow_id, probeDir: probe_dir, runKey: Date.now() });
    } catch (e) {
      toast.error(apiErrorMessage(e, t("authProfiles.verify.failed")));
      setTesting(false);
    }
  }

  // SSE 观测到 scan_end → 拉终态 + refresh profile（落最新 verify_status）+ 清 liveRun 回到回看态
  async function handleComplete(workflowId: string, probeDir: string) {
    if (!workspace || !pid || !cid) return;
    try {
      const s = await getVerifyStatus(workspace, pid, cid, workflowId, probeDir);
      if (s.state === "success") toast.success(t("authProfiles.verify.success"));
      else toast.error(t("authProfiles.verify.failed"));
    } catch (e) {
      toast.error(apiErrorMessage(e, t("authProfiles.verify.failed")));
    } finally {
      setTesting(false);
      setLiveRun(null);
      recoveredWfRef.current = null;   // 清恢复守卫，允许下次 run 恢复
      setRefreshTick((n) => n + 1);   // 重拉 profile → hasHistory 用新 run → 回看 effect 拉新 log
    }
  }

  const replayState: DashboardState = replay.reduce(dashboardReducer, emptyState());

  if (!workspace || !pid || !cid) return null;

  return (
    <div className="space-y-4">
      <Link
        to={`/p/${workspace}/auth-profiles`}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-primary"
      >
        <ArrowLeft className="size-3.5" /> {t("authProfiles.process.back")}
      </Link>

      {loading ? <Skeleton className="h-28 w-full" />
       : error ? <Card className="p-6 text-sm text-destructive">{error}</Card>
       : !credential ? <Card className="p-6 text-sm text-muted-foreground">{t("authProfiles.process.notFound")}</Card>
       : (
        <>
          {/* header: 档案名 + login_url + 角色·用户名 + 状态 + 上次验证时间 + 测试按钮 */}
          <Card className="p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 space-y-1">
                <h2 className="break-all font-mono text-lg font-medium">{profileName}</h2>
                <p className="break-all font-mono text-xs text-muted-foreground">{profileLoginUrl}</p>
                <div className="flex flex-wrap items-center gap-2 pt-1">
                  <span className="font-mono text-sm">{credential.role} · {credential.username}</span>
                  <Badge variant="outline" className={`gap-1 font-mono ${sv.cls}`}>
                    <span aria-hidden>{sv.icon}</span>{t(`authProfiles.verify.${st}`)}
                  </Badge>
                  {vs?.last_verified_at && (
                    <span className="text-xs text-muted-foreground">{fmtTime(vs.last_verified_at)}</span>
                  )}
                </div>
              </div>
              <Button variant="cta" onClick={onTest} disabled={testing} className="shrink-0">
                {testing
                  ? <><Loader2 className="size-4 animate-spin" /> {t("authProfiles.testing")}</>
                  : t("authProfiles.test")}
              </Button>
            </div>
            {st === "failed" && (vs?.failure_point || vs?.failure_detail) && (
              <div className="mt-3">
                <VerifyFailureNote failurePoint={vs?.failure_point} failureDetail={vs?.failure_detail} />
              </div>
            )}
          </Card>

          {/* 过程主体: 实时(liveRun) > 回看(replay) > 加载中 > 空态 */}
          <Card className="p-4">
            <h3 className="mb-3 text-sm font-medium text-muted-foreground">{t("authProfiles.verify.process")}</h3>
            {liveRun ? (
              <VerifyLivePanel
                key={liveRun.runKey}
                ws={workspace} pid={pid} cid={cid}
                workflowId={liveRun.workflowId} probeDir={liveRun.probeDir}
                onComplete={handleComplete}
              />
            ) : replay.length > 0 ? (
              <div className="space-y-2">
                <DashboardPanel state={replayState} elapsedMs={0} eventsCount={replay.length} />
                <LogStream events={replay} />
              </div>
            ) : hasHistory ? (
              <Skeleton className="h-32 w-full" />
            ) : (
              <p className="text-sm text-muted-foreground">{t("authProfiles.process.noRun")}</p>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
