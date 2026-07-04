// === ndjson 事件 schema（主 spec §ndjson 三方硬契约）===
// 通用字段每行必有；各 type 附加字段见主 spec 表。

export type EventCategory =
  | "PHASE" | "STEP" | "AGENT" | "TOOL" | "LLM" | "ERROR"
  | "INFO" | "WARN" | "RESUME" | "SUMMARY" | "HEADER" | "GITNEXUS" | "CONTROL";

interface CommonFields {
  ts: string;          // ISO8601 UTC 毫秒
  category: EventCategory;
}

export interface WorkflowHeaderEvent extends CommonFields {
  type: "WorkflowHeader";
  workflow_id: string; target_url: string; repo_path: string;
  mode: string; web_ui_url: string; logs_cmd: string; workspace: string;
}
export interface PhaseEvent extends CommonFields {
  type: "PhaseEvent"; phase: string; event: "start" | "complete";
  steps: string[]; step_intents: string[];
}
export interface StepEvent extends CommonFields {
  type: "StepEvent"; name: string; phase: string; event: "start" | "complete";
  duration_ms?: number; error?: string; intent?: string;
}
export interface AgentEvent extends CommonFields {
  type: "AgentEvent"; agent_name: string; event: "start" | "end";
  attempt: number; duration_ms?: number; cost_usd?: number;
  success?: boolean; error?: string;
}
export interface ToolCallEvent extends CommonFields {
  type: "ToolCallEvent"; agent_name: string; tool_name: string; parameters?: unknown;
}
export interface LlmTurnEvent extends CommonFields {
  type: "LlmTurnEvent"; agent_name: string; turn: number; content: string;
}
export interface InfoEvent extends CommonFields {
  type: "InfoEvent"; message: string; level: "info" | "warning";
}
export interface ErrorEvent extends CommonFields {
  type: "ErrorEvent"; error_type: string; message: string; context?: string;
  classified?: string; display_retryable?: boolean; attempt?: number;
  max_attempts?: number; detail_path?: string;
}
export interface SummaryEvent extends CommonFields {
  type: "SummaryEvent"; status: string; total_duration_ms?: number;
  total_cost_usd?: number; agents?: Array<{ name: string; duration_ms?: number; cost_usd?: number; success?: boolean }>;
  error?: string;
}
export interface ResumeEvent extends CommonFields {
  type: "ResumeEvent"; previous_workflow_id: string; new_workflow_id: string;
  checkpoint_hash: string; completed_agents: string[];
}
export interface GitnexusLlmEvent extends CommonFields {
  type: "GitnexusLlmEvent";
  // 字段随 events.py GitnexusLlmEvent；前端按需透传
  [k: string]: unknown;
}
export interface ScanEndEvent extends CommonFields {
  type: "scan_end"; status: "completed" | "failed" | "killed" | "crashed";
  returncode?: number; stderr_tail?: string;
}
export interface CorrelationProgressEvent extends CommonFields {
  type: "correlation_progress"; node: "repo" | "edge"; name: string;
  status: "started" | "completed" | "failed"; detail?: string;
}

export type NdjsonEvent =
  | WorkflowHeaderEvent | PhaseEvent | StepEvent | AgentEvent | ToolCallEvent
  | LlmTurnEvent | InfoEvent | ErrorEvent | SummaryEvent | ResumeEvent
  | GitnexusLlmEvent | ScanEndEvent | CorrelationProgressEvent;

// === API 响应类型（对齐 backend-design.md）===
export type WorkspaceStatus = "running" | "completed" | "failed" | "killed" | "crashed";

export interface Workspace {
  name: string;
  scan_type: "whitebox" | "blackbox" | "correlation";
  status: WorkspaceStatus;          // 归一后（见 §3.1 status 矛盾兜底）
  created_at: number;               // unix
  completed_at?: number | null;
  vuln_count?: number;
  total_cost_usd?: number;
  total_duration_ms?: number;
  links?: { parent_workspace?: string | null; child_workspaces?: string[] };
  is_correlation?: boolean;
}

export interface SessionMetrics {
  total_duration_ms: number;
  total_cost_usd: number;
  // 阶段集动态（NodeGoat: pre-recon/recon/vulnerability-analysis/reporting）
  phases: Record<string, {
    duration_ms: number; duration_percentage: number; cost_usd: number; agent_count: number;
  }>;
  agents: Record<string, {
    duration_ms: number; cost_usd: number; success: boolean;
    attempt_number: number; model: string; error?: string;
  }>;
}

export interface SessionData {
  web_url?: string; repo_path?: string; created_at?: number;
  scan_type?: string;
  status?: string;                // 顶层（可能未回写）
  completed_at?: number | null;
  links?: { parent_workspace?: string | null; child_workspaces?: string[] };
  metrics?: SessionMetrics;
  session?: { status?: string; createdAt?: string; id?: string };  // 嵌套旧格式
}

export type MergeSource = "llm-only" | "gitnexus-only" | "both" | string;

export interface Vulnerability {
  ID: string;
  vulnerability_type: string;
  externally_exploitable: boolean;
  confidence?: string;
  source_endpoint?: string;
  vulnerable_code_location?: string;
  vulnerable_parameter?: string;
  merge_source?: MergeSource;       // exploitation_queue 独有
  missing_defense?: string;
  exploitation_hypothesis?: string;
  suggested_exploit_technique?: string;
  notes?: string;
  // exploitation_queue 里常 null 的字段（保留可选）
  evidence_chain?: unknown; source_track?: unknown;
  witness_payload?: string | null; path?: string | null; verdict?: string | null;
}

export interface DeliverablesFile {
  path: string;        // 相对 deliverables/{track}/ 的路径
  size: number;
  kind: "md" | "exploitation_queue" | "llm_queue" | "gitnexus_queue"
      | "empty_json" | "big_json" | "other_json" | "other";
}

export interface DeliverablesSummary {
  track: "whitebox" | "blackbox";
  files: DeliverablesFile[];
  // 聚合用：跨所有 *_exploitation_queue.json 的 vulnerabilities
  aggregated_vulnerabilities: Vulnerability[];
  notes?: { injection_has_no_queue?: boolean };
}

export interface ScanRequest {
  type: "whitebox" | "blackbox" | "correlation";
  source?: { kind: "path" | "git"; value: string; branch?: string; commit?: string; force_reclone?: boolean };
  url?: string;
  workspace_name?: string;
  reuse_latest_whitebox?: boolean;   // 黑盒 --latest
  config_yaml?: string;              // 联动手写
  config_name?: string;              // 联动从已有选
}

export interface ScanResponse {
  workspace: string;
}

export interface FsEntry {
  name: string;
  type: "dir" | "file";
  size?: number;
  mtime?: number;
}
export interface FsBrowseResult {
  path: string;
  parent: string | null;
  entries: FsEntry[];
  truncated?: boolean;
}
