/**
 * 浏览器端文本文件下载（Blob + <a download>，用后 revoke）。
 * 报告 md 原文下载走此通道：后端 /report 已返回无截断全文，前端直接
 * 把已在内存的 md 落成 .md 文件，无需后端附件头。
 */
export function downloadTextFile(filename: string, text: string, mime = "text/markdown"): void {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/**
 * 报告下载文件名：{scanId}[-run-{runId}]-report[-{track}].md。
 * 单报告视图无 track/run；组合三 tab 带 track；黑盒/融合子 tab 选中 run 时带 runId
 * （与报告请求 path 的 run 派生条件一致）。
 */
export function reportDownloadFilename(
  scanId: string, track?: string, runId?: string | null,
): string {
  const runPart = runId ? `-run-${runId}` : "";
  const trackPart = track ? `-${track}` : "";
  return `${scanId}${runPart}-report${trackPart}.md`;
}

/**
 * 产物文件下载文件名：path 首段是 track 时带前缀（如
 * whitebox/report/comprehensive_report.md → whitebox-comprehensive_report.md），
 * 区分组合扫描三桶同名报告；run 级 strip 路径 / legacy 平铺无 track 段 → 仅 basename。
 */
const TRACK_SEGMENTS = new Set(["whitebox", "blackbox", "combined"]);

export function deliverablesDownloadFilename(path: string): string {
  const segs = path.split("/");
  const prefix = segs.length > 1 && TRACK_SEGMENTS.has(segs[0]) ? `${segs[0]}-` : "";
  return `${prefix}${segs[segs.length - 1]}`;
}
