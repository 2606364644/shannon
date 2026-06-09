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


class PropagationStep(BaseModel):
    """A single step in a taint propagation path."""
    from_func_id: str
    from_param: str
    to_func_id: str
    to_param: str
    transformation: str | None = None
    code_location: str = ""


class TaintFlow(BaseModel):
    """A complete taint flow from entry point to sink."""
    entry_point_id: str
    source_param: str
    source_type: ParameterSource
    propagation_steps: list[PropagationStep] = []
    sink_func_id: str = ""
    sink_type: SinkType | None = None


class ParameterPropagationGraph(BaseModel):
    """Complete parameter propagation graph for a repository."""
    taint_flows: list[TaintFlow] = []


# === Spec B: AST-precise sink detection ===


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
