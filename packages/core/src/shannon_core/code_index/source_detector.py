# packages/core/src/shannon_core/code_index/source_detector.py
"""入口 source 检测器(平行 sink_detector)。

对每个 entry handler 的函数体做正则匹配,识别用户可控输入取用点
(req.params.x / request.GET['x'] / $_GET['x'] / c.Query("x") / @PathParam ...),
产 SourcePoint(精确 source_type)。独立运行,不依赖 sink 存在。

与 sink_detector 对称:sink 是"危险调用点"(AST call 遍历),source 是"外部输入
取用点"(正则扫函数体——取用模式是固定文本模式,正则即可,无需 AST)。
"""
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from shannon_core.code_index.models import ParameterSource
from shannon_core.code_index.parameter_models import SourcePoint

if TYPE_CHECKING:
    from shannon_core.code_index.models import FuncBlock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceRule:
    """一条 source 取用模式规则。pattern 的 group(1) = param_name。"""
    rule_id: str
    languages: tuple[str, ...]
    pattern: re.Pattern
    source_type: ParameterSource


def _G(pattern: str) -> re.Pattern:
    """helper:包裹 param-name 捕获组的正则。"""
    return re.compile(pattern)


# ===== Default source rule library(对齐原版 Input Vector 表 5 类)=====
# 按此模式扩展其余框架:每条 = (语言, 取用模式 with group(1)=param_name, source_type)。
DEFAULT_SOURCE_RULES: tuple[SourceRule, ...] = (
    # --- Express / Node.js(typescript/javascript)---
    SourceRule("ts-express-path", ("typescript", "javascript"),
               _G(r"req\.params\.([A-Za-z_]\w*)"), ParameterSource.PATH_PARAM),
    SourceRule("ts-express-query", ("typescript", "javascript"),
               _G(r"req\.query\.([A-Za-z_]\w*)"), ParameterSource.QUERY_PARAM),
    SourceRule("ts-express-body", ("typescript", "javascript"),
               _G(r"req\.body\.([A-Za-z_]\w*)"), ParameterSource.BODY_FIELD),
    SourceRule("ts-express-header", ("typescript", "javascript"),
               _G(r"req\.(?:headers|header)\.([A-Za-z_]\w*)"), ParameterSource.HEADER),
    SourceRule("ts-express-cookie", ("typescript", "javascript"),
               _G(r"req\.cookies\.([A-Za-z_]\w*)"), ParameterSource.COOKIE),

    # --- Django / Flask(python)---
    SourceRule("py-django-get", ("python",),
               _G(r"request\.GET\[['\"]([A-Za-z_]\w*)['\"]\]"), ParameterSource.QUERY_PARAM),
    SourceRule("py-django-post", ("python",),
               _G(r"request\.POST\[['\"]([A-Za-z_]\w*)['\"]\]"), ParameterSource.BODY_FIELD),
    SourceRule("py-flask-args", ("python",),
               _G(r"request\.args\[['\"]([A-Za-z_]\w*)['\"]\]"), ParameterSource.QUERY_PARAM),
    SourceRule("py-flask-form", ("python",),
               _G(r"request\.form\[['\"]([A-Za-z_]\w*)['\"]\]"), ParameterSource.BODY_FIELD),
    SourceRule("py-flask-json", ("python",),
               _G(r"request\.json\[['\"]([A-Za-z_]\w*)['\"]\]"), ParameterSource.BODY_FIELD),

    # --- PHP ---
    SourceRule("php-get", ("php",),
               _G(r"\$_GET\[['\"]([A-Za-z_]\w*)['\"]\]"), ParameterSource.QUERY_PARAM),
    SourceRule("php-post", ("php",),
               _G(r"\$_POST\[['\"]([A-Za-z_]\w*)['\"]\]"), ParameterSource.BODY_FIELD),
    SourceRule("php-request", ("php",),
               _G(r"\$_REQUEST\[['\"]([A-Za-z_]\w*)['\"]\]"), ParameterSource.QUERY_PARAM),

    # --- Go Gin ---
    SourceRule("go-gin-query", ("go",),
               _G(r"c\.Query\(['\"]([A-Za-z_]\w*)['\"]\)"), ParameterSource.QUERY_PARAM),
    SourceRule("go-gin-param", ("go",),
               _G(r"c\.Param\(['\"]([A-Za-z_]\w*)['\"]\)"), ParameterSource.PATH_PARAM),
    SourceRule("go-gin-postform", ("go",),
               _G(r"c\.PostForm\(['\"]([A-Za-z_]\w*)['\"]\)"), ParameterSource.BODY_FIELD),

    # --- Java Spring(注解式参数,在签名或参数声明上)---
    SourceRule("java-request-param", ("java",),
               _G(r"@RequestParam(?:\([^)]*\))?\s+\w+\s+([A-Za-z_]\w*)"),
               ParameterSource.QUERY_PARAM),
    SourceRule("java-path-variable", ("java",),
               _G(r"@PathVariable(?:\([^)]*\))?\s+\w+\s+([A-Za-z_]\w*)"),
               ParameterSource.PATH_PARAM),
)


def _line_of(text: str, offset: int) -> int:
    """offset(0-based) 所在的 1-based 行号(相对于文本起始)。"""
    return text.count("\n", 0, offset) + 1


def _detect_validation(text: str, match_offset: int) -> str:
    """best-effort:取用点附近是否有简单 validation(parseInt/Number/已知 regex/escape)。"""
    window = text[max(0, match_offset - 80): match_offset + 80].lower()
    if re.search(r"parseint|int\(|number\(|float\(", window):
        return "parseInt/Number"
    if re.search(r"escape\(|encodeuri|htmlspecialchars|sanitize", window):
        return "escape/sanitize"
    if re.search(r"test\(|match\(/.+/", window):
        return "regex"
    return "NONE"


def detect_sources(
    blocks: "list[FuncBlock]",
    parser,
    entry_point_ids: "set[str]",
    *,
    source_provider: "Callable[[FuncBlock], bytes | None]",
) -> list[SourcePoint]:
    """对 entry handler 扫描函数体,识别用户可控取用点 → SourcePoint 列表。

    只对 block.id ∈ entry_point_ids 的函数跑(source 识别不被 sink 驱动;
    但只 entry handler 接收外部输入,内部函数的 tainted 参数归 chain_propagator)。
    """
    out: list[SourcePoint] = []
    for block in blocks:
        if block.id not in entry_point_ids:
            continue
        source = source_provider(block)
        text = (source.decode("utf-8", errors="replace") if source
                else block.source_code)
        for rule in DEFAULT_SOURCE_RULES:
            if block.language not in rule.languages:
                continue
            for m in rule.pattern.finditer(text):
                param_name = m.group(1)
                rel_line = _line_of(text, m.start())
                abs_line = block.start_line + rel_line - 1
                out.append(SourcePoint(
                    id=f"{block.id}::{param_name}::{abs_line}",
                    entry_point_id=block.id,
                    param_name=param_name,
                    source_type=rule.source_type,
                    expression=m.group(0),
                    file_path=block.file_path,
                    line=abs_line,
                    validation=_detect_validation(text, m.start()),
                    confidence=0.9,
                    rule_id=rule.rule_id,
                    needs_review=False,
                ))
    return _dedup(out)


def _dedup(points: list[SourcePoint]) -> list[SourcePoint]:
    """按 (entry_point_id, param_name, source_type) 去重,保留首个。"""
    seen: set[tuple] = set()
    out: list[SourcePoint] = []
    for sp in points:
        key = (sp.entry_point_id, sp.param_name, sp.source_type)
        if key in seen:
            continue
        seen.add(key)
        out.append(sp)
    return out
