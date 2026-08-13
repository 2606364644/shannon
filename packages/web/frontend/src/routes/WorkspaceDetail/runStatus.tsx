import type { ReactElement } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

/**
 * 黑盒 run 失败原因可读化 + run 状态后缀（spec 2026-08-14 §可见性）。
 *
 * 后端 run session 的 bb_reason / 任务 session bb_runs[].reason 是面向机器的技术串（如
 * "workspace provider config incomplete; missing: SUPERNOVA_OPENAI_API_KEY"）。本模块把它
 * 归类为可读类别，供 RunFailureBanner 渲染标题 + hint + 引导动作。
 */

const RUNS = "workspaceDetail.scans.runs";

export type RunReasonCategory = "providerMissing" | "authFailed" | "generic";
export type RunReasonAction = "wsSettings" | "rerunAuth" | null;

interface ReasonMeta {
  titleKey: string;
  hintKey: string; // "" → generic 用原始 reason 作 hint
  action: RunReasonAction;
}

const REASON_META: Record<RunReasonCategory, ReasonMeta> = {
  providerMissing: {
    titleKey: `${RUNS}.reasonProviderMissing.title`,
    hintKey: `${RUNS}.reasonProviderMissing.hint`,
    action: "wsSettings",
  },
  authFailed: {
    titleKey: `${RUNS}.reasonAuthFailed.title`,
    hintKey: `${RUNS}.reasonAuthFailed.hint`,
    action: "rerunAuth",
  },
  generic: {
    titleKey: `${RUNS}.reasonGeneric.title`,
    hintKey: "",
    action: null,
  },
};

/** 把机器 reason 串归类为可读类别（前端纯字符串匹配，非语义解析）。 */
export function categorizeRunReason(reason: string | null | undefined): RunReasonCategory {
  const r = reason ?? "";
  if (/provider config incomplete|SUPERNOVA_.*API_KEY/i.test(r)) return "providerMissing";
  if (/auth_failed/i.test(r)) return "authFailed";
  return "generic";
}

/** run 是否处于「失败/跳过」终态——需展示失败横幅的判定（failed/crashed/killed/skipped）。 */
export function isRunFailureStatus(status: string | null | undefined): boolean {
  return status === "failed" || status === "crashed" || status === "killed" || status === "skipped";
}

/** run status → i18n key（run selector option 后缀）。非终态/未知 → null（不加后缀）。 */
export function runStatusLabelKey(status: string | null | undefined): string | null {
  switch (status) {
    case "failed":
    case "crashed":
    case "killed":
      return `${RUNS}.statusFailed`;
    case "running":
      return `${RUNS}.statusRunning`;
    case "skipped":
      return `${RUNS}.statusSkipped`;
    case "completed":
    case "done":
      return `${RUNS}.statusCompleted`;
    default:
      return null;
  }
}

/** run 是否处于终态（可删除）；pending/running/未知 → false（运行中禁删，对齐后端 409）。 */
export function isRunTerminal(status: string | null | undefined): boolean {
  return status === "completed" || status === "done" || status === "failed"
    || status === "crashed" || status === "killed" || status === "skipped";
}

/** run 失败横幅：标题 + hint + 引导动作（providerMissing → 工作区设置链接）。
 *  reason 空 → 不渲染（调用方通常还需配合 status 终态判断）。复用 ErrorState 的 destructive class 口径。 */
export function RunFailureBanner({
  reason,
  ws,
}: {
  reason: string | null | undefined;
  ws?: string;
}): ReactElement | null {
  const { t } = useTranslation();
  if (!reason) return null;
  const cat = categorizeRunReason(reason);
  const meta = REASON_META[cat];
  const hint = cat === "generic" ? reason : t(meta.hintKey);
  return (
    <div
      role="alert"
      data-testid="run-failure-banner"
      className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive"
    >
      <div className="font-medium">{t(meta.titleKey)}</div>
      {hint && <div className="mt-0.5 text-xs opacity-90">{hint}</div>}
      {meta.action === "wsSettings" && ws && (
        <Link
          to={`/p/${ws}/settings`}
          className="mt-2 inline-flex h-8 items-center rounded-md border border-destructive/40 bg-background px-3 text-xs font-medium text-destructive hover:bg-destructive/5"
        >
          {t(`${RUNS}.bannerWsSettings`)}
        </Link>
      )}
    </div>
  );
}
