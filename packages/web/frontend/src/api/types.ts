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
  attempt: number; duration_ms?: number; cost_usd?: number; cost_currency?: string;
  input_tokens?: number; output_tokens?: number; cache_read_tokens?: number; cache_creation_tokens?: number;
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
  total_cost_usd?: number; cost_currency?: string;
  total_input_tokens?: number; total_output_tokens?: number;
  total_cache_read_tokens?: number; total_cache_creation_tokens?: number;
  agents?: Array<{ name: string; duration_ms?: number; cost_usd?: number; cost_currency?: string;
    success?: boolean; input_tokens?: number; output_tokens?: number;
    cache_read_tokens?: number; cache_creation_tokens?: number }>;
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
export interface LogEventEvent {
  ts: string;
  // category=levelname 动态值(INFO/WARNING/ERROR/DEBUG), 非 EventCategory 枚举(只有 WARN 无 WARNING)
  category: string;
  type: "LogEvent";
  logger_name: string;
  level: string;       // "INFO" | "WARNING" | "ERROR" | "DEBUG" | "NOTSET"
  message: string;
  exc_txt?: string;
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
  | GitnexusLlmEvent | ScanEndEvent | CorrelationProgressEvent | LogEventEvent;

// === API 响应类型（对齐 backend-design.md）===
export type WorkspaceStatus =
  | "running" | "in-progress" | "interrupted"
  | "completed" | "failed" | "killed" | "crashed";

export interface Workspace {
  name: string;
  scan_type: "whitebox" | "blackbox" | "correlation";
  status: WorkspaceStatus;          // 归一后（见 §3.1 status 矛盾兜底）= latest scan 聚合
  created_at: number;               // unix（= latest scan created_at）
  completed_at?: number | null;
  vuln_count?: number;
  total_cost_usd?: number;
  cost_currency?: string;
  total_duration_ms?: number;
  links?: { parent_workspace?: string | null; child_workspaces?: string[] };
  is_correlation?: boolean;
  // ws-scan 解耦（spec §5.3）：ws 是容器，scan_count/latest_* 从该 ws 的 scans 聚合。
  // 旧后端（Phase 1 未上线）不返这些字段 -> 可选，消费方 null-safe。
  scan_count?: number;
  latest_status?: WorkspaceStatus | string;
  latest_created_at?: number;
}

/**
 * ws 内单个 scan 的摘要（spec §4.2）。GET /workspaces/{ws}/scans 返该数组；
 * GET /workspaces/{ws} 的 scans[] 兼容字段也用此 shape。
 *
 * Phase 1 scan_store.ScanSummary 额外聚合了 vuln_counts/total_duration_ms/links/
 * is_correlation（供 ws 列表行从 latest scan 聚合），此处设可选兼容，前端按需用。
 */
export interface ScanSummary {
  scan_id: string;
  scan_type: "whitebox" | "blackbox" | "correlation";
  status: WorkspaceStatus | string;   // 归一后（终态优先 + heartbeat）
  created_at: number;                  // unix
  completed_at?: number | null;
  vuln_count: number;
  total_cost_usd?: number | null;
  cost_currency?: string | null;
  is_running: boolean;
  // temporal workflow 标识 {ws}-{scan_id}[-resume-N]（前端「扫描任务名」展示用，替代纯日期 scan_id）。
  // 旧后端不返 -> 可选，消费方 ?? scan_id 兜底。
  workflow_id?: string;
  // Phase 1 额外聚合字段（兼容，spec §4.2 未列但 scan_store 已产出）
  vuln_counts?: Record<string, number>;
  total_duration_ms?: number | null;
  links?: { parent_workspace?: string | null; child_workspaces?: string[] };
  is_correlation?: boolean;
  // IA 重设计 §3：跨 ws 聚合（GET /api/scans）注入的归属工作区名。per-ws listScans 不返此字段。
  workspace?: string;
}

export interface SessionMetrics {
  // 以下字段运行时可能缺失:session.py create_workspace 初始仅写 {"agents":{}},
  // normalize_metrics 不补 phases/totals(phases 透传不动);pre-recon 产出后才齐。
  // 故全可选,消费方(OverviewTab)须 null-safe —— Object.entries/keys(x ?? {})。
  total_duration_ms?: number;
  total_cost_usd?: number;
  cost_currency?: string;
  total_input_tokens?: number; total_output_tokens?: number;
  total_cache_read_tokens?: number; total_cache_creation_tokens?: number;
  // 阶段集动态（NodeGoat: pre-recon/recon/vulnerability-analysis/reporting）
  phases?: Record<string, {
    duration_ms: number; duration_percentage: number; cost_usd: number; agent_count: number;
    cost_currency?: string;
    input_tokens?: number; output_tokens?: number; cache_read_tokens?: number; cache_creation_tokens?: number;
  }>;
  agents?: Record<string, {
    duration_ms: number; cost_usd: number; cost_currency?: string; success: boolean;
    attempt_number: number; model: string; error?: string;
    input_tokens?: number; output_tokens?: number; cache_read_tokens?: number; cache_creation_tokens?: number;
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
  workflow_id?: string;  // temporal workflow 标识（ScanDetail header 任务名展示）
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

/** 漏洞危害等级（前端推断，非后端权威字段——见 lib/vuln-block.ts inferSeverity）。 */
export type Severity = "Critical" | "High" | "Medium" | "Low";

/** 从 markdown `### XXX-VULN-NN` 块解析出的单个 kv 字段。 */
export interface ParsedVulnField {
  key: string;
  val: string;
}

/**
 * 从报告 markdown 解析出的单个漏洞块（`### XXX-VULN-NN — 标题` + 后续 kv-list + witness fenced code）。
 * 由 splitByVulnBlocks + parseVulnBlock 产出，供报告渲染（MarkdownView 按严重度着色 + 完整原始 markdown 渲染）、inferSeverity 推断等级。
 */
export interface ParsedVulnBlock {
  id: string;                       // "XSS-VULN-04"
  prefix: string;                   // "XSS"（ID 前缀，用于类型着色）
  title: string;                    // 标题描述（去 ★ 后）
  starred: boolean;                 // 标题含 ★ 首要标记
  vulnType: string;                 // vulnerability_type 字段值
  fields: ParsedVulnField[];        // 块内 kv-list
  witnessPayload?: string;          // fenced code 内容（PoC）
  externallyExploitable: boolean | null;
  authRequired: boolean | null;
  confidence: string | null;        // 归一化小写：high | med | low | null
  verdict: string | null;
  raw: string;                      // 原始块 markdown（调试）
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
  source?: { kind: "repo" | "path"; value: string };
  url?: string;
  // final-review C2: 字段名必须与 backend ScanRequest (models.py:25) 一致 = `workspace`。
  // pydantic v2 默认不容未知键, 旧 `workspace_name` 会被静默丢弃 -> req.workspace=None -> 422。
  workspace?: string;
  reuse_latest_whitebox?: boolean;
  config_yaml?: string;
  config_name?: string;
}

export interface ScanResponse {
  workspace: string;
  // ws-scan 解耦（spec §5.2）：POST /api/scan 的 ScanAccepted 增 scan_id。
  // 可选--过渡期 Phase 1 未上线时后端不返 scan_id，前端 F4 回退跳旧 ws-scoped live 路由。
  scan_id?: string;
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

export type RepoState = "ready" | "cloning" | "pulling" | "failed" | "stale";

export interface Repo {
  name: string;
  group?: string | null;  // 分组名（如 frontend/backend）；扁平仓库为 null
  source?: { kind: "git" | "linked" | "unknown" | string; url?: string; branch?: string; commit?: string };
  state: RepoState;
  /** 关联仓库（admin 按绝对路径关联的已存在目录，非本 ws 私有克隆）→ true；只读（禁 pull/checkout）。 */
  linked?: boolean;
  size_bytes?: number;
  cloned_at?: string;
  last_pull_at?: string;
  last_error?: string | null;
  progress?: number | null;
}

export interface RepoDetail extends Repo {
  recent_events?: Array<Record<string, unknown>>;
}
