// verify failed 的失败说明渲染（AuthProfileTestPage 进度区 + VerifyProcessPage header 共用）。
// 按 failure_point 显示「标题 + 行动指引」，原始 failure_detail 折叠为"技术详情"——
// 2026-08-17 前用户看到的是裸异常串（如 PentestError: Error code: 401 - {...}），
// 误以为是目标站账号密码问题，实际是 LLM 引擎配置错配。
//
// 历史数据兜底：旧记录 failure_point=out_of_band 但 detail 含 LLM 引擎签名
// （"Error code: 401/403/429"、「令牌已过期」）→ 按 engine 渲染，不重跑也能看到正确指引。
// 前端兜底只匹配确定性的 SDK 签名（不含 quota/rate limit 等泛化词，
// 防止目标站自身的报错文案被误判——后端分类器只作用于异常串，无此风险）。
import { useTranslation } from "react-i18next";

const ENGINE_DETAIL_RE = /error code: 40[139]|令牌已过期/i;

function effectiveFailurePoint(point?: string | null, detail?: string | null): string {
  if (point === "engine") return "engine";
  if ((!point || point === "out_of_band") && detail && ENGINE_DETAIL_RE.test(detail)) {
    return "engine";
  }
  return point || "out_of_band";
}

function engineSubcode(detail?: string | null): "401" | "403" | "429" | null {
  const m = detail?.match(/error code: (40[139])/i);
  return (m?.[1] as "401" | "403" | "429") ?? null;
}

export function VerifyFailureNote({ failurePoint, failureDetail }: {
  failurePoint?: string | null;
  failureDetail?: string | null;
}) {
  const { t } = useTranslation();
  const point = effectiveFailurePoint(failurePoint, failureDetail);
  const sub = point === "engine" ? engineSubcode(failureDetail) : null;
  return (
    <div className="border-l-2 border-red/60 bg-red/10 px-2.5 py-1.5 text-xs leading-relaxed text-red/80">
      <p className="font-medium">{t(`authProfiles.verify.failure.${point}.title`)}</p>
      <p>{t(`authProfiles.verify.failure.${point}.hint`)}
        {sub && <span className="block">{t(`authProfiles.verify.failure.engine.${sub}`)}</span>}
      </p>
      {failureDetail && (
        <details className="mt-1">
          <summary className="cursor-pointer select-none opacity-80 hover:opacity-100">
            {t("authProfiles.verify.failure.detailLabel")}
          </summary>
          <p className="mt-1 break-all font-mono opacity-70">{failureDetail}</p>
        </details>
      )}
    </div>
  );
}
