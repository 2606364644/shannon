import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, Download } from "lucide-react";
import {
  blackboxRunDeliverablesPath, scanDeliverablesPath,
  blackboxRunDeliverablesDownloadUrl, scanDeliverablesDownloadUrl,
} from "../../api/client";
import { useApiText } from "@/api/useApiResource";
import type { DeliverablesFile, Vulnerability } from "../../api/types";
import { MarkdownView } from "../../components/MarkdownView";
import { VulnCard } from "../../components/VulnCard";
import { ErrorState } from "../../components/ErrorState";
import { CopyButton } from "../../components/CopyButton";
import { deliverablesDownloadFilename } from "@/lib/download";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

// big_json 按需加载的展示截断上限：后端无 range 支持，全量拉取后前端只渲染首段防卡 UI。
const BIG_JSON_DISPLAY_LIMIT = 256 * 1024;

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * 产物文件预览舞台（产物 tab 重设计 2026-08-17）：占主区域的展示位，按 kind 分派渲染——
 * md → MarkdownView；exploitation_queue → 结构化 VulnCard 列表（解析失败回退原文）；
 * 其余 JSON → 格式化 <pre>；empty_json/other → 诚实空态（不 fetch）；big_json →
 * 默认提示 +「仍要加载」按需拉取（展示截断）。内容走 useApiText（SWR），
 * 来回切换文件秒显缓存。父组件以 key={file.path} 挂载，切换文件即重置内部态。
 */
export function FileStage({ ws, scanId, file, runId, onBack }: {
  ws: string;
  scanId: string;
  file: DeliverablesFile;
  runId?: string | null;
  onBack: () => void;
}) {
  const { t } = useTranslation();
  const isBig = file.kind === "big_json";
  const [bigLoaded, setBigLoaded] = useState(false);

  const fetchable =
    file.kind === "md" || file.kind === "other_json" || file.kind.endsWith("_queue") ||
    (isBig && bigLoaded);
  const path = fetchable
    ? (runId
        ? blackboxRunDeliverablesPath(ws, scanId, runId, file.path)
        : scanDeliverablesPath(ws, scanId, file.path))
    : null;
  const { text, loading, error } = useApiText(path);

  const truncated = isBig && bigLoaded && text.length > BIG_JSON_DISPLAY_LIMIT;
  const shownText = truncated ? text.slice(0, BIG_JSON_DISPLAY_LIMIT) : text;

  return (
    <div className="min-w-0 space-y-4" data-testid="file-stage">
      <div className="flex flex-wrap items-center gap-2 border-b border-border pb-3">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="size-3.5" /> {t("workspaceDetail.deliverables.backToAgg")}
        </Button>
        <span className="min-w-0 truncate font-mono text-sm font-semibold" title={file.path}>
          {file.path}
        </span>
        <Badge variant="outline" className="font-mono text-xs text-muted-foreground">{file.kind}</Badge>
        <span className="text-xs text-muted-foreground">{formatBytes(file.size)}</span>
        {text && <CopyButton value={shownText} ariaLabel={t("workspaceDetail.deliverables.copyContent")} />}
        {/* 下载：走后端 ?download=1 附件（磁盘原文），所有 kind 可下——与 CopyButton
            不同（复制需先有预览内容），empty_json/other/未加载的 big_json 也能下载。 */}
        <Button variant="ghost" size="sm" asChild className="text-muted-foreground">
          <a
            href={runId
              ? blackboxRunDeliverablesDownloadUrl(ws, scanId, runId, file.path)
              : scanDeliverablesDownloadUrl(ws, scanId, file.path)}
            download={deliverablesDownloadFilename(file.path)}
          >
            <Download aria-hidden />
            {t("workspaceDetail.deliverables.download")}
          </a>
        </Button>
      </div>
      {error ? (
        <ErrorState message={t("workspaceDetail.deliverables.fileLoadError", { error })} />
      ) : file.kind === "empty_json" ? (
        <div className="text-sm text-muted-foreground">{t("workspaceDetail.deliverables.emptyJson")}</div>
      ) : file.kind === "other" ? (
        <div className="text-sm text-muted-foreground">{t("workspaceDetail.deliverables.unsupportedKind")}</div>
      ) : isBig && !bigLoaded ? (
        <div className="space-y-2">
          <div className="text-sm text-yellow">
            {t("workspaceDetail.deliverables.bigJson", { size: file.size })}
          </div>
          <Button variant="outline" size="sm" onClick={() => setBigLoaded(true)}>
            {t("workspaceDetail.deliverables.loadAnyway")}
          </Button>
        </div>
      ) : loading ? (
        <div className="space-y-2">
          {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-5 w-full" />)}
        </div>
      ) : file.kind === "md" ? (
        <div className="space-y-2">
          <MarkdownView markdown={text} />
          {isServerTruncated(text) && (
            <div className="rounded-md border border-yellow/40 bg-yellow/10 p-2 text-xs text-yellow">
              {t("workspaceDetail.deliverables.serverTruncated")}
            </div>
          )}
        </div>
      ) : file.kind === "exploitation_queue" ? (
        <QueueView text={text} />
      ) : (
        <pre className="overflow-x-auto rounded-md bg-muted/50 p-3 font-mono text-xs break-all whitespace-pre-wrap">
          {prettyJson(shownText)}
          {truncated ? `\n\n${t("workspaceDetail.deliverables.truncated", { size: BIG_JSON_DISPLAY_LIMIT })}` : ""}
        </pre>
      )}
    </div>
  );
}

/** exploitation_queue JSON → VulnCard 列表；非 {vulnerabilities:[]} 结构回退原文。 */
function QueueView({ text }: { text: string }) {
  try {
    const parsed = JSON.parse(text) as { vulnerabilities?: unknown };
    if (Array.isArray(parsed.vulnerabilities)) {
      const vulns = parsed.vulnerabilities as Vulnerability[];
      return (
        <div className="space-y-2">
          {vulns.map((v) => <VulnCard key={v.ID} v={v} />)}
        </div>
      );
    }
  } catch {
    // 非合法 JSON（或空文本）→ 走原文回退
  }
  return (
    <pre className="overflow-x-auto rounded-md bg-muted/50 p-3 font-mono text-xs break-all whitespace-pre-wrap">
      {text}
    </pre>
  );
}

// 后端截断标注（spec 2026-08-18 preview_limit）：超阈文件 PlainTextResponse 返回截断 + 尾标。
function isServerTruncated(text: string): boolean {
  return text.includes("[truncated:");
}

/** JSON 文本格式化展示；非法 JSON 原样返回。 */
function prettyJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}
