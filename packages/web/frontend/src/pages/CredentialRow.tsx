// Task 12: 凭据行 = 状态徽章(success=绿✓ / failed=红✗ / unverified=黄●) + 「测试登录」按钮 + verify-status 轮询。
// 流程:点击按钮 → testCredential(POST) → 每 pollMs(默认 3s, 最多 40 次 ≈ 120s)轮询 getVerifyStatus(GET) →
//       503=未就绪继续轮询,非 503 ApiError=真实失败停止+toast,200=完成(按 state 分别 toast);
//       成功后 onChanged() 触发 AuthProfilesPage refresh(GET list 返回后端持久化的新 verify_status)。
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { testCredential, getVerifyStatus } from "@/api/authProfiles";
import type { AuthProfile, AuthProfileCredential, VerifyState } from "@/api/types";
import { ApiError } from "@/api/client";
import { apiErrorMessage } from "@/lib/apiError";

const MAX_POLLS = 40;
const DEFAULT_POLL_MS = 3000;

interface Props {
  ws: string;
  profile: AuthProfile;
  credential: AuthProfileCredential;
  onChanged: () => void;
  /** 轮询间隔 ms,默认 3000(生产 ~120s 上限);测试可注入小值。 */
  pollMs?: number;
}

export function CredentialRow({ ws, profile, credential, onChanged, pollMs = DEFAULT_POLL_MS }: Props) {
  const { t } = useTranslation();
  const [testing, setTesting] = useState(false);
  const st: VerifyState = credential.verify_status?.state ?? "unverified";
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
          // 503 = Temporal 结果未就绪,继续轮询;其它 ApiError = 真实失败,停止 + toast
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
    <div className="flex items-center gap-2">
      <Badge variant="outline" className={`gap-1 font-mono ${badgeCls}`}>
        <span aria-hidden>{icon}</span>{t(`authProfiles.verify.${st}`)}
      </Badge>
      <span className="font-mono">{credential.role} · {credential.username}</span>
      {st === "failed" && credential.verify_status?.failure_detail && (
        <span className="text-xs text-red/80">{credential.verify_status.failure_detail}</span>
      )}
      <Button
        size="sm"
        variant="outline"
        onClick={onTest}
        disabled={testing}
        title={t("authProfiles.testHint")}
      >
        {testing
          ? <><Loader2 className="size-3 animate-spin" /> {t("authProfiles.testing")}</>
          : t("authProfiles.test")}
      </Button>
    </div>
  );
}
