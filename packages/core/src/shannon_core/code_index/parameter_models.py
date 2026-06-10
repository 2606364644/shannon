"""Parameter propagation models for taint flow analysis.

Provides models for tracking how user input flows through function calls
to security-sensitive sinks.
"""

import logging
from enum import Enum
from pydantic import BaseModel

from shannon_core.code_index.models import ParameterSource

logger = logging.getLogger(__name__)


class SinkType(str, Enum):
    """Security-sensitive sink types for taint analysis."""
    SQL_EXECUTION = "sql_execution"
    COMMAND_EXEC = "command_exec"
    DESERIALIZATION = "deserialization"
    FILE_WRITE = "file_write"
    TEMPLATE_RENDER = "template_render"
    HTTP_REQUEST = "http_request"
    LOG_WRITE = "log_write"
    UNKNOWN = "unknown"


class SlotContext(str, Enum):
    """Sink 输入位的安全上下文 —— 呼应原始项目的 slot 类型系统。"""
    SQL_VALUE = "sql_value"            # SQL-val/like/num —— 需参数绑定
    SQL_IDENTIFIER = "sql_identifier"  # SQL-enum/ident —— 需白名单
    CMD_ARGUMENT = "cmd_argument"      # 需数组参数 + shell=False / shlex.quote
    FILE_PATH = "file_path"            # 需白名单路径 / resolve+边界检查
    TEMPLATE_EXPR = "template_expr"    # SSTI —— 需沙箱+autoescape
    URL = "url"                        # SSRF —— 需协议/主机白名单
    DESERIALIZE_OBJ = "deserialize"    # 需可信来源+HMAC
    GENERIC = "generic"                # 未细分


class PropagationStep(BaseModel):
    """A single step in a taint propagation path."""
    step_id: str = ""                 # "{flow_id}#s{n}"
    from_func_id: str
    from_param: str
    to_func_id: str
    to_param: str
    transformation: str | None = None  # "concat" / "encode" / "format" / "sanitize_hint:<name>" / None
    code_location: str = ""            # "{file}:{line}"
    confidence: float = 1.0            # 本步映射的可信度


class TaintFlow(BaseModel):
    """A complete taint flow from entry point to sink.

    Spec A 升级（Spec B §3.4 预留契约）：
    - 用 sink_call_site_id 指向具体的 SinkCallSite.id
    - sink_slot / tainted_arg_index 描述到达的精确槽位
    - confidence = 整条链最弱步
    - has_sanitizer_hint 仅提示，不判有效性（有效性由 Spec C 的 LLM）
    - notes 显式标注不完备（如"未追踪容器字段"）

    旧字段 sink_func_id / sink_type 保留为遗留兼容（旧测试 / 旧 param_graph.json
    反序列化时仍可读）。新生产代码不应再写入它们。
    """
    flow_id: str = ""                 # "{entry_point_id}->{sink_call_site_id}"
    entry_point_id: str
    source_param: str
    source_type: ParameterSource
    propagation_steps: list[PropagationStep] = []

    # 新：Spec A 精确终点
    sink_call_site_id: str = ""
    sink_slot: SlotContext = SlotContext.GENERIC
    tainted_arg_index: int = -1       # -1 = 未约束 / variadic
    confidence: float = 1.0
    has_sanitizer_hint: bool = False
    notes: str = ""

    # 遗留：保留默认值供旧测试 / 旧 json 反序列化
    sink_func_id: str = ""
    sink_type: SinkType | None = None


class ParameterPropagationGraph(BaseModel):
    """Complete parameter propagation graph for a repository.

    language_coverage: 实际跑过传播的语言（如 ["python", "typescript"]）。
    skipped_languages: typed param 提取暂未支持、跳过传播的语言（如
        ["go", "java", "php"]）— Spec C 据此提示 LLM。
    """
    taint_flows: list[TaintFlow] = []
    language_coverage: list[str] = []
    skipped_languages: list[str] = []


class TaintPath(BaseModel):
    """LLM 返回的单条 taint 传播路径。"""
    source_param: str
    sink_id: str
    sink_arg_index: int
    intermediate_vars: list[str] = []
    sanitized: bool = False
    sanitizer_description: str | None = None
    confidence: float = 1.0


class TaintAnalysisResult(BaseModel):
    """LLM 返回的函数级 taint 分析结果（structured output schema）。"""
    tainted_params: list[str] = []
    propagation_paths: list[TaintPath] = []


class IntraResult(BaseModel):
    """函数内 taint 分析的规范化输出。LLM 或确定性分析均产出此格式。"""
    tainted_params: set[str] = set()
    hits: dict[str, float] = {}   # sink_id → confidence
    local_steps: list[PropagationStep] = []


# === Spec B: AST-precise sink detection ===
# (SlotContext moved above PropagationStep to resolve TaintFlow forward ref)


class DangerousSlot(BaseModel):
    """sink 调用中一个需要防御的参数位。"""
    arg_index: int            # 第几个实参（0-based）；-1 表示 variadic/spread 整体
    slot: SlotContext
    expression: str           # 该实参的源码表达式文本（供 Spec A/LLM 追踪）
    is_entry_hint: bool       # AST 能直接看出该实参源自函数参数/外部输入（浅判断）


class SinkCategory(str, Enum):
    """主分类，与 SinkType 并存。"""
    SQL = "sql"
    COMMAND = "command"
    FILE = "file"
    TEMPLATE = "template"
    DESERIALIZATION = "deserialization"
    SSRF = "ssrf"
    XSS = "xss"              # 仅 code-level（innerHTML/document.write 等）
    LOG = "log"
    REDIRECT = "redirect"


class SinkCallSite(BaseModel):
    """一次具体的危险函数调用 —— 三 spec 共享枢纽。

    id 格式："{file}:{caller_func}:{callee}:{line}:{col}"，Spec A 的
    `TaintFlow.sink_call_site_id` 必须用此格式。
    """
    id: str
    caller_id: str                          # 所在 FuncBlock.id
    callee_name: str                        # 方法/函数名，如 "execute"
    callee_receiver: str | None             # receiver，如 "cursor" / "subprocess"；裸函数为 None
    category: SinkCategory
    sink_subtype: str                       # 细分类型，如 "sql_raw_query" / "ssrf_http_client"
    file_path: str
    line: int
    column: int
    dangerous_slots: list[DangerousSlot]    # 规则库标注的危险参数位 + slot
    rule_id: str                            # 命中的规则 id（可追溯到规则库定义）
    needs_review: bool = False              # best-effort 判定 / 动态调用 / 模板类，需 LLM 复核
