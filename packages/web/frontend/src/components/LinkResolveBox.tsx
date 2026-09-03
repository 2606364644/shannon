import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { resolveLink } from "@/api/client";
import type { ResolveLinkResult } from "@/api/types";
import { apiErrorMessage } from "@/lib/apiError";

interface Props {
  /** 选定工作区（组件由父级在 ws 已选时才渲染）。 */
  workspace: string;
  /** 限定接受的链接类别：MR 表单传 ["mr"]——收到仓库链接时行内提示切白盒，
   *  不回调不回填。白盒表单不传（MR 链接回调后由页面自动切类型）。 */
  accepts?: Array<"mr" | "repo">;
  /** 解析成功（且通过 accepts 过滤）回调——页面统一处理：回填 repo/refs、MR 链接
   *  切类型、repo_state=cloning 时启动页面级下载提示（CloneWatch，不随表单切换卸载）。 */
  onResolved: (r: ResolveLinkResult) => void;
}

/** 统一链接解析框（2026-09-03 仓库入口整合 B 段）：粘贴 GitLab 仓库/MR 链接 →
 *  解析回填。白盒与 MR 两张表单共用。
 *
 *  解析失败行内报错（后端 detail 透传），不阻塞手填路径。下载进度不在本组件
 *  （表单切换会卸载实例丢轮询态）——cloning 提示由页面级 CloneWatch 承担。
 */
export function LinkResolveBox({ workspace, accepts, onResolved }: Props) {
  const { t } = useTranslation();
  const [url, setUrl] = useState("");
  const [resolving, setResolving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onResolve() {
    const trimmed = url.trim();
    if (!trimmed || resolving) return;
    setResolving(true);
    setError(null);
    try {
      const r = await resolveLink(workspace, trimmed);
      if (accepts && !accepts.includes(r.kind)) {
        setError(t("scan.link.repoInMrHint"));
        return;
      }
      onResolved(r);
    } catch (e) {
      setError(apiErrorMessage(e, t("scan.link.resolveFailed")));
    } finally {
      setResolving(false);
    }
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <span className="h-3 w-[3px] rounded-full bg-primary" aria-hidden />
        <span className="text-[11px] font-semibold text-muted-foreground">{t("scan.link.inputLabel")}</span>
      </div>
      <div className="flex items-center gap-2">
        <Input
          data-testid="link-url-input"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void onResolve(); } }}
          placeholder={t("scan.link.placeholder")}
          size="sm"
          className="font-mono flex-1 min-w-0"
        />
        <Button
          type="button"
          data-testid="link-resolve-btn"
          variant="outline"
          size="sm"
          className="shrink-0 text-xs"
          disabled={!url.trim() || resolving}
          onClick={() => void onResolve()}
        >
          {resolving ? t("scan.link.resolving") : t("scan.link.resolveBtn")}
        </Button>
      </div>
      {error && <div className="text-destructive text-xs">{error}</div>}
    </div>
  );
}
