import type { NdjsonEvent } from "./types";

export type SseStatus = "open" | "closed" | "error";
export interface SseSnapshot {
  events: NdjsonEvent[];
  status: SseStatus;
  lastEventId?: string;
  version: number;
}

/** 尾部保留条数（spec §7.1）：LogStream 虚拟化阈值 500 的 10 倍余量。 */
const CAP = 5000;

const EMPTY_SNAPSHOT: SseSnapshot = { events: [], status: "closed", version: 0 };

/** jsdom 无 rAF 时的降级（setTimeout 宏任务）。 */
const scheduleRaf = (cb: () => void): number =>
  typeof requestAnimationFrame === "function"
    ? requestAnimationFrame(cb)
    : (setTimeout(cb, 0) as unknown as number);
const cancelRaf = (id: number): void => {
  if (typeof cancelAnimationFrame === "function") cancelAnimationFrame(id);
  else clearTimeout(id as unknown as ReturnType<typeof setTimeout>);
};

/** useSyncExternalStore 协议的 SSE 外部 store（spec §E）：
 *  - rAF 批量：onmessage 只入 pending 并调度一次，flush 时合并追加 + 重建快照
 *    （两次 flush 之间 getSnapshot 引用恒定——useSyncExternalStore 的硬要求）。
 *  - 环形缓冲：events 尾部截断至 CAP，消除逐条数组复制与无界增长。
 *  - 引用计数：订阅归零自动关连接并出 Map；StrictMode 双挂载安全。 */
class ScanEventStore {
  private listeners = new Set<() => void>();
  private pending: NdjsonEvent[] = [];
  private events: NdjsonEvent[] = [];
  private status: SseStatus = "closed";
  private lastEventId?: string;
  private snapshot: SseSnapshot = EMPTY_SNAPSHOT;
  private es: EventSource | null = null;
  private rafId = 0;
  private refs = 0;

  constructor(private url: string, private stopType: string) {}

  subscribe(cb: () => void): () => void {
    this.refs++;
    this.listeners.add(cb);
    this.connect();
    return () => {
      this.listeners.delete(cb);
      this.refs--;
      if (this.refs <= 0) {
        this.es?.close();
        this.es = null;
        stores.delete(`${this.stopType}::${this.url}`);
      }
    };
  }

  getSnapshot(): SseSnapshot {
    return this.snapshot;
  }

  private connect(): void {
    if (this.es) return;
    const Es = (globalThis as { EventSource?: typeof EventSource }).EventSource;
    if (!Es) return;
    const es = new Es(this.url);
    this.es = es;
    this.status = "open";
    this.flush(); // 发布初始 open 状态
    es.onopen = () => { this.status = "open"; this.flush(); };
    es.onerror = () => { this.status = "error"; this.flush(); };
    es.onmessage = (e: MessageEvent) => {
      let ev: NdjsonEvent;
      try { ev = JSON.parse(String(e.data)) as NdjsonEvent; } catch { return; }
      if (e.lastEventId) this.lastEventId = e.lastEventId;
      if (ev.type === this.stopType) { this.status = "closed"; es.close(); }
      this.pending.push(ev);
      this.scheduleFlush();
    };
  }

  private scheduleFlush(): void {
    if (this.rafId) return;
    this.rafId = scheduleRaf(() => { this.rafId = 0; this.flush(); });
  }

  private flush(): void {
    if (this.rafId) { cancelRaf(this.rafId); this.rafId = 0; }
    if (this.pending.length) {
      const merged = this.events.concat(this.pending);
      this.events = merged.length > CAP ? merged.slice(merged.length - CAP) : merged;
      this.pending = [];
    }
    this.snapshot = {
      events: this.events,
      status: this.status,
      lastEventId: this.lastEventId,
      version: this.snapshot.version + 1,
    };
    for (const cb of this.listeners) cb();
  }
}

const stores = new Map<string, ScanEventStore>();

/** 按 `${stopType}::${url}` 取/建单例 store（连接在首个 subscribe 时惰性建立）。 */
export function getScanEventStore(url: string, stopType: string): ScanEventStore {
  const key = `${stopType}::${url}`;
  let s = stores.get(key);
  if (!s) { s = new ScanEventStore(url, stopType); stores.set(key, s); }
  return s;
}

/** 测试隔离：强制清空全部 store（同文件多测试共享模块级 Map）。 */
export function _resetScanEventStoresForTests(): void {
  stores.clear();
}

export { EMPTY_SNAPSHOT };
