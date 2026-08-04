// events.ndjson 的 ts 字段时区归一化解析。
//
// 铁证背景（2026-08-04 delivery-20260804-024910）：
// worker 容器时区=UTC（docker-compose 未设 TZ，容器默认 UTC；宿主机是 CST +08:00）。
// format_log_time()（display/formatters.py:42-44）用 datetime.now() 写 ndjson ts，
// 产出 worker 容器 UTC 墙钟，格式 "2026-08-04 02:49:13"（无时区后缀、空格分隔）。
// 前端裸 Date.parse 把无时区串按浏览器本地时区解释 -> UTC+8 用户多算 8h（开扫即显 8h+）。
//
// parseEventTs 把无时区串当 UTC（与 worker 容器 UTC 一致）；带时区串（Z/+00:00）原样。
// P2（structured_event_renderer 改 format_timestamp）后新扫描 ts 带 Z，本函数仍兼容。

/**
 * 把 events.ndjson 的 ts 字符串解析为 UTC epoch 毫秒。
 *
 * - 无时区串（"2026-08-04 02:49:13" 空格 / "2026-08-04T02:49:13" T 分隔）-> 当 UTC
 *   （worker 容器历来 UTC，与 time.time()/format_timestamp 的 UTC 一致）。
 * - 带时区串（"...Z" / "...+00:00"）-> 原样 Date.parse（已是 UTC，标准）。
 * - 无效/空 -> NaN。
 */
export function parseEventTs(ts: string | null | undefined): number {
  if (!ts) return NaN;
  // 已带时区标记（Z / 偏移 ±HH:MM）-> 标准解析，不干预。
  if (/[zZ]$/.test(ts) || /[+-]\d{2}:\d{2}$/.test(ts)) {
    return Date.parse(ts);
  }
  // 无时区串 -> 当 UTC：统一成 ISO T 分隔 + "Z"。
  // 兼容空格分隔（"YYYY-MM-DD HH:MM:SS"）与 T 分隔（"YYYY-MM-DDTHH:MM:SS"）。
  const iso = ts.replace(" ", "T") + "Z";
  return Date.parse(iso);
}
