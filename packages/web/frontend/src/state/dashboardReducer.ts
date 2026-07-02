// 1:1 复刻 packages/core/src/shannon_core/display/dashboard_state.py:70-132 DashboardState.apply。
// 前端独立可信的 SSE 状态累积器（spec §4.1）。纯函数、不可变、无 IO、无时间调用。
import type { NdjsonEvent } from "../api/types";
import { firstNonemptyLine, humanizeToolCall } from "./formatters";

export type AgentStatus = "running" | "done" | "failed";

export interface AgentRow {
  name: string;
  status: AgentStatus;
  attempt: number;
  turn: number;
  last_action: string | null;
  last_action_detail: string | null;
  last_turn_text: string | null;
  duration_ms: number | null;
  cost_usd: number | null;
  error: string | null;
}

export interface DashboardState {
  current_phase: string | null;
  agents: Record<string, AgentRow>;
  phase_units: string[];
  unit_status: Record<string, string>;
  unit_intent: Record<string, string>;
  // 派生（core 是 @property；TS 在 reducer 末尾计算并挂上，便于组件直接读）
  completed_count: number;
  total_cost: number;
  total_units: number;
  completed_units: number;
  running_units: string[];
}

export function emptyState(): DashboardState {
  return {
    current_phase: null, agents: {}, phase_units: [], unit_status: {}, unit_intent: {},
    completed_count: 0, total_cost: 0, total_units: 0, completed_units: 0, running_units: [],
  };
}

function row(name: string): AgentRow {
  return {
    name, status: "running", attempt: 1, turn: 0,
    last_action: null, last_action_detail: null, last_turn_text: null,
    duration_ms: null, cost_usd: null, error: null,
  };
}

// 对齐 DashboardState._set_unit：仅当 name ∈ phase_units 才更新，未声明的 unit 忽略。
function setUnit(state: DashboardState, name: string, status: string): DashboardState {
  if (!state.phase_units.includes(name)) return state;
  return { ...state, unit_status: { ...state.unit_status, [name]: status } };
}

// 对齐 DashboardState 的 @property 派生属性。
function derive(s: DashboardState): DashboardState {
  const agents = Object.values(s.agents);
  return {
    ...s,
    completed_count: agents.filter((a) => a.status === "done" || a.status === "failed").length,
    total_cost: agents.reduce((sum, a) => sum + (a.cost_usd ?? 0), 0),
    total_units: s.phase_units.length,
    completed_units: Object.values(s.unit_status).filter((st) => st === "done" || st === "failed").length,
    running_units: s.phase_units.filter((n) => s.unit_status[n] === "running"),
  };
}

/**
 * 1:1 复刻 core DashboardState.apply：fold 一个 ndjson 事件，返回新 state（不可变）。
 * 6 个状态变化分支（PhaseEvent / StepEvent / ResumeEvent / AgentEvent / ToolCallEvent /
 * LlmTurnEvent）+ 其余 type 无 dashboard 状态变化（return state）。
 */
export function dashboardReducer(state: DashboardState, event: NdjsonEvent): DashboardState {
  let next: DashboardState = state;

  switch (event.type) {
    case "PhaseEvent": {
      if (event.event === "start") {
        // 对齐 core：intents = {n: i for n,i in zip(steps, step_intents) if i}
        const intents: Record<string, string> = {};
        for (let i = 0; i < event.steps.length; i++) {
          const it = event.step_intents[i];
          if (it) intents[event.steps[i]] = it;
        }
        next = {
          ...state,
          current_phase: event.phase,
          phase_units: [...event.steps],
          unit_status: {},
          unit_intent: intents,
        };
      } else {
        // complete: keep units（对齐 core）
        next = { ...state, current_phase: event.phase };
      }
      break;
    }

    case "StepEvent": {
      const status: string = event.event === "start" ? "running" : (event.error ? "failed" : "done");
      let s = setUnit(state, event.name, status);
      if (event.intent) {
        s = { ...s, unit_intent: { ...s.unit_intent, [event.name]: event.intent } };
      }
      next = s;
      break;
    }

    case "ResumeEvent": {
      const agents = { ...state.agents };
      for (const name of event.completed_agents) {
        agents[name] = { ...row(name), status: "done" };
      }
      next = { ...state, agents };
      break;
    }

    case "AgentEvent": {
      const agents = { ...state.agents };
      const cur = agents[event.agent_name] ?? row(event.agent_name);
      if (event.event === "start") {
        agents[event.agent_name] = { ...cur, status: "running", attempt: event.attempt, error: null };
        next = setUnit(state, event.agent_name, "running");
      } else {
        // 对齐 core：success 默认 falsy → failed；但 event.success 可能 undefined，
        // core 用 `event.success`（truthy 判定），TS 用 === false 对齐"未带 success 视为失败"。
        // 实际 core AgentEvent.success 字段语义：end 时通常显式给 True/False；
        // 为对齐 core `event.success`（None 为 falsy → failed），保持 === false 不变。
        const status: AgentStatus = event.success === false ? "failed" : "done";
        agents[event.agent_name] = {
          ...cur,
          status,
          duration_ms: event.duration_ms ?? cur.duration_ms,
          cost_usd: event.cost_usd ?? cur.cost_usd,
          error: event.error ?? null,
        };
        next = setUnit(state, event.agent_name, status);
      }
      next = { ...next, agents };
      break;
    }

    case "ToolCallEvent": {
      const agents = { ...state.agents };
      const cur = agents[event.agent_name];
      if (cur) {
        const detail = humanizeToolCall(event.tool_name, event.parameters ?? {});
        agents[event.agent_name] = { ...cur, last_action: event.tool_name, last_action_detail: detail };
      }
      next = { ...state, agents };
      break;
    }

    case "LlmTurnEvent": {
      const agents = { ...state.agents };
      const cur = agents[event.agent_name];
      if (cur) {
        const line = firstNonemptyLine(event.content);
        agents[event.agent_name] = {
          ...cur,
          turn: event.turn,
          last_turn_text: line || cur.last_turn_text,
        };
      }
      next = { ...state, agents };
      break;
    }

    // ErrorEvent / SummaryEvent / WorkflowHeader / InfoEvent / GitnexusLlmEvent /
    // ScanEndEvent / CorrelationProgressEvent → 无 dashboard 状态变化（对齐 core default）。
    default:
      next = state;
  }

  return derive(next);
}
