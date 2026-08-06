import { describe, it, expect } from "vitest";
import { parseEventTs, fmtClock, fmtLocalFull } from "./eventTs";

// 铁证背景（2026-08-04 delivery-20260804-024910）：
// worker 容器时区=UTC（compose 未设 TZ），format_log_time() 用 datetime.now() 写 ndjson ts，
// 格式 "2026-08-04 02:49:13"（无时区后缀、空格分隔）。
// 前端 Date.parse 按浏览器本地时区解释 -> UTC+8 用户多算 8h。
// parseEventTs 须把无时区串当 UTC（与 worker 容器 UTC 一致），带时区串原样解析。

describe("parseEventTs - 时区归一化", () => {
  it("无时区空格分隔串（生产 ndjson 普通事件）当 UTC 解析", () => {
    const ts = "2026-08-04 02:49:13"; // worker UTC 墙钟
    const ms = parseEventTs(ts);
    // 期望 epoch 对应 UTC 02:49:13（非浏览器本地时区解释）
    const expected = Date.UTC(2026, 7, 4, 2, 49, 13);
    expect(ms).toBe(expected);
  });

  it("带 Z 后缀 ISO（新 P2 扫描 / scan_end web 回退）原样解析", () => {
    const ts = "2026-08-04T02:49:13.789Z";
    const ms = parseEventTs(ts);
    const expected = Date.parse(ts); // 带 Z 已是 UTC，原样
    expect(ms).toBe(expected);
  });

  it("带 +00:00 偏移 ISO（scan_end web 回退 _now_iso）原样解析为 UTC", () => {
    const ts = "2026-08-04T01:43:12.547577+00:00";
    const ms = parseEventTs(ts);
    expect(ms).toBe(Date.UTC(2026, 7, 4, 1, 43, 12, 547));
  });

  it("无时区 T 分隔串当 UTC 解析（兼容）", () => {
    const ts = "2026-08-04T02:49:13"; // T 分隔但无时区
    const ms = parseEventTs(ts);
    expect(ms).toBe(Date.UTC(2026, 7, 4, 2, 49, 13));
  });

  it("历史串与带 Z 串解析到同一 UTC 时刻（等价）", () => {
    // 同一时刻的两种写法应解析为同一 epoch
    const noTz = parseEventTs("2026-08-04 02:49:13");
    const withZ = parseEventTs("2026-08-04T02:49:13Z");
    expect(noTz).toBe(withZ);
  });

  it("无效串返回 NaN", () => {
    expect(Number.isNaN(parseEventTs("not-a-time"))).toBe(true);
    expect(Number.isNaN(parseEventTs(""))).toBe(true);
  });

  it("空串/null 安全返回 NaN", () => {
    expect(Number.isNaN(parseEventTs(null as unknown as string))).toBe(true);
  });
});

// fmtClock / fmtLocalFull：把 parseEventTs 产出的 UTC epoch 渲染成浏览器本地时区可读串。
//
// 背景（2026-08-06 hk-user-view live 页日志行时差）：
// LogStream 窄列旧 tsClock 只正则抠 ts 的 HH:MM:SS 原样显示 = worker 容器 UTC 墙钟，
// 对 UTC+8 用户差 8h（04:20:20 实为本地 12:20:20）。ts 经 _normalize_ts 已带 Z（自描述 UTC），
// 但 tsClock 不解析时区、直接显 UTC 时分秒 -> 误导。修复：tsClock 走 parseEventTs -> epoch ->
// fmtClock 渲染本地时区。这里测纯函数（传固定 timeZone，硬断言，不依赖运行环境时区）。
describe("fmtClock / fmtLocalFull - UTC epoch -> 本地时区可读串", () => {
  // UTC 2026-08-06 04:20:20（用户报告的"不对"的那个 UTC 时刻）
  const ms = Date.UTC(2026, 7, 6, 4, 20, 20);

  it("fmtClock 固定 Asia/Shanghai：UTC 04:20:20 -> CST 12:20:20", () => {
    expect(fmtClock(ms, "Asia/Shanghai")).toBe("12:20:20");
  });

  it("fmtClock 固定 UTC：04:20:20 -> 04:20:20（不漂移）", () => {
    expect(fmtClock(ms, "UTC")).toBe("04:20:20");
  });

  it("fmtClock 输出恒为 HH:MM:SS（24h，无 AM/PM）", () => {
    expect(fmtClock(ms, "Asia/Shanghai")).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });

  it("fmtLocalFull 固定 Asia/Shanghai 含本地日期 + 12:20:20", () => {
    const s = fmtLocalFull(ms, "Asia/Shanghai");
    expect(s).toContain("2026");
    expect(s).toContain("12:20:20");
  });

  it("fmtLocalFull 固定 UTC 含 04:20:20（不漂移）", () => {
    expect(fmtLocalFull(ms, "UTC")).toContain("04:20:20");
  });
});
