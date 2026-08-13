// === ndjson 事件 schema（主 spec §ndjson 三方硬契约）===
// 通用字段每行必有；各 type 附加字段见主 spec 表。

export type EventCategory =
  | "PHASE" | "STEP" | "AGENT" | "TOOL" | "LLM" | "ERROR"
  | "INFO" | "WARN" | "RESUME" | "SUMMARY" | "HEADER" | "GITNEXUS" | "CONTROL";

interface CommonFields {
  ts: string;          // 事件时间戳。历史 ndjson 为 worker 容器 UTC 墙钟 "YYYY-MM-DD HH:MM:SS"（无时区）；
                       // P2 后新扫描为 UTC ISO8601 带 Z。前端经 utils/eventTs.parseEventTs 归一化当 UTC 解析。
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
  // 组合扫描（spec 2026-08-12 §6.2）：白盒+黑盒一键组合，scan_type 仍是 whitebox，靠 session.combined 标记。
  // combined=true 时卡片收起显 progress_pct + 阶段名（bb_phase 映射），展开按需读 events 推步级。
  // 纯白盒/纯黑盒不返这些字段 -> 可选，消费方 null-safe（非 combined 走原单段渲染，零回归）。
  combined?: boolean;
  bb_phase?: string;        // precheck | pending | running | completed | failed | skipped
  bb_reason?: string;       // 失败/跳过原因（precheck fail / skipped 无产物 等）
  progress_pct?: number;    // 后端预算（三阶段加权，spec §9.2）；前端只显示不重算
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
  // 服务端墙钟基准（unix 秒）——_scan_detail 返 time.time()。前端用它做时钟 offset 校正
  // （server_now*1000 - Date.now()），消除浏览器/服务端时钟不同步时「总耗时负数」的根因。
  server_now?: number;
  scan_type?: string;
  status?: string;                // 顶层（可能未回写）
  completed_at?: number | null;
  links?: { parent_workspace?: string | null; child_workspaces?: string[] };
  metrics?: SessionMetrics;
  session?: { status?: string; createdAt?: string; id?: string };  // 嵌套旧格式
  workflow_id?: string;  // temporal workflow 标识（ScanDetail header 任务名展示）
  // 重跑预填用（_scan_detail 补返）：白盒 repo 名 / 黑盒复用白盒 scan_id / 黑盒登录配置。
  source_repo?: string | null;
  reuse_whitebox_scan_id?: string | null;
  authentication?: ScanAuthentication | null;
  // auth-profile-vault（Task 14）：profile 模式重跑预填——后端 _scan_detail 暂未返此字段，
  // 前端先就位（补返时自动生效）。与 authentication 互斥。
  auth_profile_id?: string | null;
  // 多角色子集（2026-08-06）：profile 模式选多个角色，空=全选该档案所有角色。
  auth_credential_ids?: string[] | null;
}

export type MergeSource = "llm-only" | "gitnexus-only" | "both" | string;

export interface Vulnerability {
  ID: string;
  vulnerability_type: string;
  externally_exploitable: boolean;
  confidence?: string;
  title?: string;            // 一句话描述性标题（spec 2026-08-06）；空时退化 vulnerability_type
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

/** 黑盒登录配置（对齐 core Authentication schema：models/config.py:29-45）。
 *  字段名（snake_case）与后端 pydantic 模型一致——scan_manager Authentication.model_validate 校验。*/
export interface ScanAuthentication {
  login_type: "form" | "sso" | "api" | "basic";
  login_url: string;
  credentials: {
    username: string;
    password?: string;
    totp_secret?: string;
    email_login?: { address: string; password: string; totp_secret?: string };
  };
  login_flow?: string[];
}

// === 认证档案库（auth-profile-vault, Task 10 契约）===
// 对齐 backend auth_profile_store / auth_profiles.py 响应 payload。
export type VerifyState = "unverified" | "running" | "success" | "failed";
export interface VerifyStatus {
  state: VerifyState;
  failure_point?: "username_or_password" | "totp_secret" | "out_of_band";
  failure_detail?: string;
  last_verified_at?: string;
  // 块3c：最近一次验证的 probe 目录 + workflow_id（verify-log 定位 + 下次覆盖清理）。
  probe_dir?: string;
  workflow_id?: string;
}
export interface AuthProfileCredential {
  id: string;
  role: string;
  username: string;
  password?: string;        // GET 返 "••••" if 有值（后端不回传明文）
  totp_secret?: string;
  email_login?: { address: string; password?: string; totp_secret?: string };
  verify_status: VerifyStatus;
}
export interface AuthProfile {
  id: string;
  name: string;
  login_url: string;
  login_type: "form" | "sso" | "api" | "basic";
  login_flow?: string[];
  credentials: AuthProfileCredential[];
  created_at?: string;
  updated_at?: string;
  scope?: "workspace" | "system";  // system = configs seed 的全局共享只读档案
}

// === HOST 档案库（blackbox-host-profile, Task 9-11 契约）===
// 对齐 backend host_profile_store / host_profiles.py 响应 payload。
// mappings: domain→IP 的 hosts 映射，黑盒扫描时注入 agent-browser 代理 / DNS 覆盖。
export interface HostMapping {
  ip: string;
  host: string;
}
export interface HostProfile {
  id: string;
  name: string;
  source_url?: string | null;   // 生成来源的 /etc/hosts 风格文本 URL（null=手填）
  mappings: HostMapping[];
  scope?: "workspace" | "system";  // system = 全局共享只读档案（configs seed）
  created_at?: string;
  updated_at?: string;
}

export interface ScanRequest {
  type: "whitebox" | "blackbox" | "correlation";
  // 扫描入口已收窄为「工作区已下载仓库」——本地路径入口移除（source.kind 恒为 repo）。
  source?: { kind: "repo"; value: string };
  url?: string;
  // final-review C2: 字段名必须与 backend ScanRequest (models.py) 一致 = `workspace`。
  // pydantic v2 默认不容未知键, 旧 `workspace_name` 会被静默丢弃 -> req.workspace=None -> 422。
  workspace?: string;
  // 黑盒「复用白盒结果」：指定要复用的白盒 scan_id（工作区内某个 whitebox scan）。
  // 黑盒恒复用白盒结果（exploitation-only），此字段必填；无 repo/standalone 分支。
  reuse_whitebox_scan_id?: string;
  // 黑盒登录配置（仅 blackbox + auth.enabled 时发送）。后端写 scan-config.yaml → blackbox workflow
  // config_path → run_blackbox_auth_validation（agent-browser 登录 + auth-state 落盘）。
  authentication?: ScanAuthentication;
  // 认证档案库（auth-profile-vault）：黑盒复用已验证的登录档案，免每次手填。
  // 后端按 auth_profile_id 加载档案、按 auth_credential_ids 选 credentials[] 中哪些角色
  // （空=全选该档案所有角色）。与上方 `authentication?` 互斥（二者全无时黑盒按 unauthenticated 处理）。
  auth_profile_id?: string;
  auth_credential_ids?: string[];
  // inline 多角色附加账号（#2，2026-08-07）：与 authentication 同存，每条 {role,username,password,totp_secret?}。
  // 后端 scan_manager 展开成 accounts[]（多身份对比）；仅 inline 模式（authentication 存在）时合法。
  auth_accounts?: { role: string; username: string; password: string; totp_secret?: string }[];
  // HOST 档案库（blackbox-host-profile）：黑盒扫描时用 host_profile_id 选档案注入 domain→IP 映射
  // （agent-browser --proxy 覆盖 DNS），或 host_url 临时拉取一份 /etc/hosts 风格文本。
  host_profile_id?: string;
  host_url?: string;
  config_yaml?: string;
  config_name?: string;
}

export interface ScanResponse {
  workspace: string;
  // ws-scan 解耦（spec §5.2）：POST /api/scan 的 ScanAccepted 增 scan_id。
  // 可选--过渡期 Phase 1 未上线时后端不返 scan_id，前端 F4 回退跳旧 ws-scoped live 路由。
  scan_id?: string;
  // 组合扫描（Task 9，spec §8.2）：后端 passthrough 的黑盒阶段标记。白盒+url 组合提交后，
  // 后端先跑黑盒认证预验证——此时 bb_phase="precheck"；前端据此显「预验证中」态。
  // 纯白盒/纯黑盒不返此字段（undefined）。
  bb_phase?: "precheck" | "running" | "done" | string;
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
