import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  getScanEventStore,
  _resetScanEventStoresForTests,
} from "./scanEventStore";

// —— 受控 rAF：手动驱动 flush ——
let rafCb: (() => void) | null = null;
// —— fake EventSource：记录实例、手动 emit ——
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  closed = false;
  onmessage: ((e: { data: string; lastEventId?: string }) => void) | null = null;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { FakeEventSource.instances.push(this); }
  close() { this.closed = true; }
  emit(type: string, extra?: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify({ type, ts: "t1", ...extra }), lastEventId: undefined });
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  rafCb = null;
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("requestAnimationFrame", (cb: () => void) => { rafCb = cb; return 1; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => {
  _resetScanEventStoresForTests();
  vi.unstubAllGlobals();
});

const flush = () => { const cb = rafCb; rafCb = null; cb?.(); };

describe("scanEventStore", () => {
  it("rAF 批量：多条事件一次 flush、一次通知", () => {
    const store = getScanEventStore("/sse", "scan_end");
    const listener = vi.fn();
    store.subscribe(listener);
    // subscribe 即发布初始 open 快照（status=open, 0 events）→ 首次通知。
    expect(listener).toHaveBeenCalledTimes(1);
    const es = FakeEventSource.instances[0]!;
    es.emit("InfoEvent"); es.emit("InfoEvent"); es.emit("StepEvent");
    expect(listener).toHaveBeenCalledTimes(1);          // flush 前零增量通知
    expect(store.getSnapshot().events).toHaveLength(0);
    flush();
    expect(listener).toHaveBeenCalledTimes(2);          // 一帧一次批量通知
    expect(store.getSnapshot().events).toHaveLength(3);
  });

  it("getSnapshot 引用稳定：两次 flush 之间恒定", () => {
    const store = getScanEventStore("/sse", "scan_end");
    store.subscribe(vi.fn());
    const es = FakeEventSource.instances[0]!;
    es.emit("InfoEvent"); flush();
    const s1 = store.getSnapshot();
    expect(store.getSnapshot()).toBe(s1);              // 同引用
    es.emit("InfoEvent"); flush();
    expect(store.getSnapshot()).not.toBe(s1);          // flush 后才换新
  });

  it("环形缓冲：超 5000 截尾保新", () => {
    const store = getScanEventStore("/sse", "scan_end");
    store.subscribe(vi.fn());
    const es = FakeEventSource.instances[0]!;
    for (let i = 0; i < 5002; i++) es.emit("InfoEvent", { seq: i });
    flush();
    const events = store.getSnapshot().events as unknown as Array<{ seq: number }>;
    expect(events).toHaveLength(5000);
    expect(events[0]!.seq).toBe(2);                    // 头部两条被裁
    expect(events[4999]!.seq).toBe(5001);              // 尾部保新
  });

  it("引用计数：最后一个 unsubscribe 关连接；再 subscribe 复用/重建", () => {
    const store = getScanEventStore("/sse", "scan_end");
    const un1 = store.subscribe(vi.fn());
    const un2 = store.subscribe(vi.fn());
    expect(FakeEventSource.instances).toHaveLength(1); // 同 store 单连接
    un1();
    expect(FakeEventSource.instances[0]!.closed).toBe(false);
    un2();
    expect(FakeEventSource.instances[0]!.closed).toBe(true); // 归零关连接
  });

  it("scan_end：status=closed + 关连接 + 事件仍入列", () => {
    const store = getScanEventStore("/sse", "scan_end");
    store.subscribe(vi.fn());
    const es = FakeEventSource.instances[0]!;
    es.emit("scan_end", { status: "completed" });
    flush();
    const snap = store.getSnapshot();
    expect(snap.status).toBe("closed");
    expect(es.closed).toBe(true);
    expect(snap.events.at(-1)!.type).toBe("scan_end");
  });

  it("同 url+stopType 单例；不同 stopType 不同 store", () => {
    expect(getScanEventStore("/sse", "scan_end")).toBe(getScanEventStore("/sse", "scan_end"));
    expect(getScanEventStore("/sse", "scan_end")).not.toBe(getScanEventStore("/sse", "clone_end"));
  });

  it("SSE id 去重：重放事件按 lastEventId 丢弃（换 rev 重开流后幂等）", () => {
    const store = getScanEventStore("/sse", "scan_end");
    store.subscribe(vi.fn());
    const es = FakeEventSource.instances[0]!;
    // 首轮：带 id 的两条事件入列
    es.onmessage?.({ data: JSON.stringify({ type: "InfoEvent", ts: "t1", seq: 1 }), lastEventId: "wb=10" });
    es.onmessage?.({ data: JSON.stringify({ type: "InfoEvent", ts: "t2", seq: 2 }), lastEventId: "wb=20" });
    flush();
    expect(store.getSnapshot().events).toHaveLength(2);
    // 重放：同 id 的事件被丢弃；新 id 正常入列；无 id 事件透传（兼容）
    es.onmessage?.({ data: JSON.stringify({ type: "InfoEvent", ts: "t1", seq: 1 }), lastEventId: "wb=10" });
    es.onmessage?.({ data: JSON.stringify({ type: "InfoEvent", ts: "t3", seq: 3 }), lastEventId: "wb=30" });
    es.onmessage?.({ data: JSON.stringify({ type: "InfoEvent", ts: "t4", seq: 4 }), lastEventId: undefined });
    flush();
    const events = store.getSnapshot().events as unknown as Array<{ seq: number }>;
    expect(events.map((e) => e.seq)).toEqual([1, 2, 3, 4]);
  });
});
