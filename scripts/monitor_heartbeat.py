#!/usr/bin/env python3
"""监控某 workspace 的 heartbeat 新鲜度,验证「心跳线程化」修复是否生效。

用法:
  python scripts/monitor_heartbeat.py <workspace_dir> [采样间隔秒, 默认5]

背景(2026-07-15 trip_1784116216):HeartbeatManager 心跳写入原用 asyncio.sleep,与
run_code_index 等 activity 共享 worker event loop;GitNexus taint/sink/source 分析的
同步 CPU 密集段阻塞 event loop ~161s,期间心跳 task 停转、heartbeat 超 90s freshness
阈值 → web 误判 interrupted(终态不可逆),而 worker 实际仍正常跑。修复:心跳写入线程化
(daemon thread + time.sleep),彻底脱离 event loop。

验证标准:扫描全程(尤其 code-index 长任务那几分钟)heartbeat age 始终 < 阈值(默认 90s
= SHANNON_SCAN_LIVENESS_SECONDS)。若出现 age>阈值 窗口(哪怕一次),说明修复未生效,
或 worker 镜像未 rebuild / 容器未重启。
"""
import os
import sys
import time
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    ws = Path(sys.argv[1])
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    liveness = float(os.environ.get("SHANNON_SCAN_LIVENESS_SECONDS", "90"))
    hb = ws / "heartbeat"

    print(f"监控 {hb}")
    print(f"liveness 阈值={liveness}s  采样间隔={interval}s  (Ctrl+C 停止)")
    print(f"{'时间':>10}  {'age(s)':>8}  状态")
    print("-" * 48)

    max_age = 0.0
    stale = 0
    samples = 0
    try:
        while True:
            now = time.time()
            ts = time.strftime("%H:%M:%S", time.localtime(now))
            try:
                age = now - hb.stat().st_mtime
                samples += 1
                max_age = max(max_age, age)
                fresh = age < liveness
                if not fresh:
                    stale += 1
                mark = "✓ fresh" if fresh else "✗ STALE ← 心跳过期,可能触发误判 interrupted"
                print(f"{ts:>10}  {age:8.1f}  {mark}", flush=True)
            except FileNotFoundError:
                print(f"{ts:>10}  {'-':>8}  缺失(heartbeat 不存在:worker 未起/已退出/尚未写首跳)",
                      flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n" + "=" * 48)
        print(f"采样 {samples} 次  max_age={max_age:.1f}s  stale 次数={stale}")
        if samples == 0:
            print("结论:全程未采样到 heartbeat —— 确认 worker 在跑、workspace 路径对。")
        elif stale == 0:
            print(f"结论:✓ 全程 fresh(max_age={max_age:.1f}s < {liveness}s),修复生效。")
        else:
            print(f"结论:✗ 出现 {stale} 次 stale(max_age={max_age:.1f}s)→ "
                  "修复未生效或 worker 未用新镜像(确认已 rebuild + 重启 worker 容器)。")


if __name__ == "__main__":
    main()
