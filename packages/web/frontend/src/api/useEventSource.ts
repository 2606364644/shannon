import { useEffect, useState } from "react";
import type { NdjsonEvent } from "./types";

export type SseStatus = "open" | "closed" | "error";
export interface UseEventSource {
  events: NdjsonEvent[]; status: SseStatus; lastEventId?: string;
}

export function useEventSource(url: string, stopType: string = "scan_end"): UseEventSource {
  const [events, setEvents] = useState<NdjsonEvent[]>([]);
  const [status, setStatus] = useState<SseStatus>("closed");
  const [lastEventId, setLastEventId] = useState<string | undefined>(undefined);

  useEffect(() => {
    const Es = (globalThis as { EventSource?: typeof EventSource }).EventSource;
    if (!Es) return;
    const es = new Es(url);
    setStatus("open");
    es.onmessage = (e: MessageEvent) => {
      const line = String(e.data);
      let ev: NdjsonEvent;
      try { ev = JSON.parse(line) as NdjsonEvent; } catch { return; }
      if (e.lastEventId) setLastEventId(e.lastEventId);
      if (ev.type === stopType) { setStatus("closed"); es.close(); }
      setEvents((prev) => [...prev, ev]);
    };
    es.onerror = () => setStatus("error");    // EventSource 内置自动重连（带 Last-Event-ID）
    es.onopen = () => setStatus("open");
    return () => es.close();
  }, [url, stopType]);

  return { events, status, lastEventId };
}
