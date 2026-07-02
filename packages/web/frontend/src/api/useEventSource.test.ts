import { describe, it, expect, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useEventSource } from "./useEventSource";

// fake EventSource：构造后可手动 emit message
class FakeES {
  static last?: FakeES;
  onmessage: ((e: { data: string; lastEventId?: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  closed = false;
  constructor(public url: string) { FakeES.last = this; }
  close() { this.closed = true; }
  emit(data: string, lastEventId?: string) { this.onmessage?.({ data, lastEventId }); }
}
vi.stubGlobal("EventSource", FakeES);

describe("useEventSource", () => {
  it("累积事件 + scan_end 关闭", () => {
    const { result } = renderHook(() => useEventSource("/api/workspaces/ws/events"));
    expect(result.current.status).toBe("open");
    act(() => FakeES.last!.emit(JSON.stringify({ ts: "t", category: "PHASE", type: "PhaseEvent", phase: "recon", event: "start", steps: [], step_intents: [] })));
    expect(result.current.events).toHaveLength(1);
    act(() => FakeES.last!.emit(JSON.stringify({ ts: "t", category: "CONTROL", type: "scan_end", status: "completed" })));
    expect(result.current.status).toBe("closed");
    expect(FakeES.last!.closed).toBe(true);
  });

  it("lastEventId 在新 message 上更新", () => {
    const { result } = renderHook(() => useEventSource("/api/workspaces/ws/events"));
    act(() => FakeES.last!.emit(
      JSON.stringify({ ts: "t", category: "PHASE", type: "PhaseEvent", phase: "recon", event: "start", steps: [], step_intents: [] }),
      "123",
    ));
    expect(result.current.lastEventId).toBe("123");
  });

  it("非 JSON 行被静默忽略（不污染 events 数组）", () => {
    const { result } = renderHook(() => useEventSource("/api/workspaces/ws/events"));
    act(() => FakeES.last!.emit("not-json"));
    expect(result.current.events).toHaveLength(0);
  });

  it("unmount 时关闭 EventSource", () => {
    const { unmount } = renderHook(() => useEventSource("/api/workspaces/ws/events"));
    const es = FakeES.last!;
    expect(es.closed).toBe(false);
    unmount();
    expect(es.closed).toBe(true);
  });
});
