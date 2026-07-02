import { FixedSizeList } from "react-window";
import type { NdjsonEvent, EventCategory } from "../api/types";

const CAT_CLASS: Partial<Record<EventCategory, string>> = {
  PHASE: "ev-phase", STEP: "ev-info", AGENT: "ev-agent", TOOL: "ev-tool",
  LLM: "ev-llm", ERROR: "ev-error", INFO: "ev-info", WARN: "ev-warn",
  RESUME: "ev-info", SUMMARY: "ev-phase", HEADER: "trace", GITNEXUS: "ev-info",
  CONTROL: "trace",
};

function tsClock(ts: string): string {
  // ISO8601 → HH:MM:SS（本地或原样取 time 部分）
  const m = /T(\d{2}:\d{2}:\d{2})/.exec(ts);
  return m ? m[1] : ts;
}

function summarize(e: NdjsonEvent): string {
  switch (e.type) {
    case "PhaseEvent": return `${e.event === "start" ? "Starting" : "Complete"} ${e.phase}`;
    case "StepEvent": return `${e.event === "start" ? "○" : "✓"} ${e.name}`;
    case "AgentEvent": return `${e.event === "start" ? "▶" : (e.success === false ? "✗" : "✓")} [${e.agent_name}]`;
    case "ToolCallEvent": return `🔧 ${e.tool_name}`;
    case "LlmTurnEvent": return `💭 turn ${e.turn}`;
    case "ErrorEvent": return `${e.error_type}: ${e.message}`;
    case "InfoEvent": return e.message;
    case "SummaryEvent": return `summary: ${e.status}`;
    case "ResumeEvent": return `resume ← ${e.previous_workflow_id}`;
    default: return e.type;
  }
}

const ROW_HEIGHT = 20;
const VIRTUAL_THRESHOLD = 500;

function Row({ index, style, data }: { index: number; style: React.CSSProperties; data: NdjsonEvent[] }) {
  const e = data[index];
  return (
    <div style={style} className={`log-row mono ${CAT_CLASS[e.category] ?? "trace"}`}>
      <span className="trace">[{tsClock(e.ts)}]</span> <span className="ev-type">{e.type}</span> {summarize(e)}
    </div>
  );
}

export function LogStream({ events }: { events: NdjsonEvent[] }) {
  if (events.length > VIRTUAL_THRESHOLD) {
    return (
      <div className="log-stream">
        <FixedSizeList
          height={400}
          width="100%"
          itemCount={events.length}
          itemSize={ROW_HEIGHT}
          itemData={events}
        >
          {Row}
        </FixedSizeList>
      </div>
    );
  }
  return (
    <div className="log-stream">
      {events.map((e, i) => (
        <div key={i} className={`log-row mono ${CAT_CLASS[e.category] ?? "trace"}`}>
          <span className="trace">[{tsClock(e.ts)}]</span> <span className="ev-type">{e.type}</span> {summarize(e)}
        </div>
      ))}
    </div>
  );
}
