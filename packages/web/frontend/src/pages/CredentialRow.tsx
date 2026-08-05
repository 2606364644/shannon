// Task 12 + 块4: 凭据行 = 状态徽章 + 「测试登录」+ verify-status 轮询 + 展开看过程(verify-log)。
// 流程:点测试登录 → testCredential → 每 pollMs 轮询 getVerifyStatus(503=running 继续,块2 非阻塞)
//       → 完成按 state toast + onChanged refresh。展开(失败默认/手动)拉 getVerifyLog 显示 agent 登录每步。
import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { testCredential, getVerifyStatus, getVerifyLog } from "@/api/authProfiles";
import type { AuthProfile, AuthProfileCredential, VerifyState } from "@/api/types";
import { ApiError } from "@/api/client";
import { apiErrorMessage } from "@/lib/apiError";

// 块2: 覆盖 workflow 最长实测 ~153s（60 次 × 3s = 180s）。配合后端非阻塞 describe,
// 每次轮询秒级返回,不再因 120s 上限把 COMPLETED+success 误判 failed。
const MAX_POLLS = 60;
const DEFAULT_POLL_MS = 3000;

interface Props {
  ws: string;
  profile: AuthProfile;
  credential: AuthProfileCredential;
  onChanged: () => void;
  /** 轮询间隔 ms,默认 3000(生产 ~180s 上限);测试可注入小值。 */
  pollMs?: number;
}

export function CredentialRow({ ws, profile, credential, onChanged, pollMs = DEFAULT_POLL_MS }: Props) {
  const { t } = useTranslation();
  const [testing, setTesting] = useState(false);
  const st: VerifyState = credential.verify_status?.state ?? "unverified";
  const vs = credential.verify_status;
  const probeDir = vs?.probe_dir;
  const workflowId = vs?.workflow_id;
  const hasLog = !!(probeDir && workflowId);
  // 块4: 失败默认展开（用户最想看失败过程）;成功/unverified 折叠,手动展开。
  const [expanded, setExpanded] = useState(st === "failed");
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);

  // 块4: 展开且有 probe_dir/workflow_id 时拉 verify-log 显示 agent 登录每步。
  useEffect(() => {
    if (!expanded || !probeDir || !workflowId) return;
    let cancelled = false;
    getVerifyLog(ws, profile.id, credential.id, workflowId, probeDir)
      .then((r) => { if (!cancelled) setEvents(r.events); })
      .catch(() => { if (!cancelled) setEvents([]); });
    return () => { cancelled = true; };
    // 依赖派生字符串（probeDir/workflowId）非 vs 对象,避免 refresh 新引用重触发。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded, probeDir, workflowId, ws, profile.id, credential.id]);

  const badgeCls =
    st === "success" ? "border-green/40 text-green"
    : st === "failed" ? "border-red/40 text-red"
    : "border-yellow/40 text-yellow";
  const icon = st === "success" ? "✓" : st === "failed" ? "✗" : "●";

  async function onTest() {
    setTesting(true);
    try {
      const { workflow_id, probe_dir } = await testCredential(ws, profile.id, credential.id);
      for (let i = 0; i < MAX_POLLS; i++) {
        await new Promise((r) => setTimeout(r, pollMs));
        try {
          const s = await getVerifyStatus(ws, profile.id, credential.id, workflow_id, probe_dir);
          // 200 = verify 完成(后端已落盘);按 state 分别 toast
          if (s.state === "success") toast.success(t("authProfiles.verify.success"));
          else toast.error(t("authProfiles.verify.failed"));
          onChanged();
          setTesting(false);
          return;
        } catch (e) {
          // 503 = workflow 仍 RUNNING(块2 非阻塞),继续轮询;其它 ApiError = 真实失败,停止 + toast
          if (e instanceof ApiError && e.status === 503) continue;
          toast.error(apiErrorMessage(e, t("authProfiles.verify.failed")));
          setTesting(false);
          return;
        }
      }
      // 超出轮询上限仍 503:判失败
      toast.error(t("authProfiles.verify.failed"));
      setTesting(false);
    } catch (e) {
      // testCredential 本身失败(网络 / 4xx / 5xx)
      toast.error(apiErrorMessage(e, t("authProfiles.verify.failed")));
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
        {hasLog && (
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
      {expanded && hasLog && (
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
