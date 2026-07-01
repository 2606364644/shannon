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
from shannon_core.code_index._rule_loader import DATA_DIR, load_yaml

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


def _build_source_rules(raw: dict) -> "tuple[SourceRule, ...]":
    """YAML dict → tuple[SourceRule]。未知 source_type fail-fast(ValueError)。"""
    rules: list[SourceRule] = []
    for item in raw.get("rules", []):
        rules.append(SourceRule(
            rule_id=item["rule_id"],
            languages=tuple(item.get("languages") or ()),
            pattern=re.compile(item["pattern"]),
            source_type=ParameterSource(item["source_type"]),
        ))
    return tuple(rules)


# ===== Default source rule library(外部化:data/source_rules.yml)=====
DEFAULT_SOURCE_RULES: tuple[SourceRule, ...] = _build_source_rules(
    load_yaml(DATA_DIR / "source_rules.yml"))


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
