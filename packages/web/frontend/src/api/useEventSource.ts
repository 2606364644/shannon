import { useCallback, useSyncExternalStore } from "react";
import { getScanEventStore, EMPTY_SNAPSHOT, type SseSnapshot } from "./scanEventStore";
import type { NdjsonEvent } from "./types";

export type { SseStatus } from "./scanEventStore";
export interface UseEventSource {
  events: NdjsonEvent[];
  status: SseSnapshot["status"];
  lastEventId?: string;
}

/** SSE 订阅 hook（spec §E）：scanEventStore 的薄包装。url 为空（scanId 未就绪）时
 *  不连接。快照由 store 维持引用稳定，useSyncExternalStore 不会空转。 */
export function useEventSource(url: string, stopType: string = "scan_end"): UseEventSource {
  // getScanEventStore 是按 key 幂等的纯 Map 访问（连接在 subscribe 时才建立），
  // 渲染期调用安全；url 为空时不取 store。
  const store = url ? getScanEventStore(url, stopType) : null;
  const subscribe = useCallback(
    (cb: () => void) => (store ? store.subscribe(cb) : () => {}),
    [store],
  );
  const getSnapshot = useCallback(
    () => (store ? store.getSnapshot() : EMPTY_SNAPSHOT),
    [store],
  );
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
