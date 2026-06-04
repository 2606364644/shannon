"""Sink classification for taint analysis.

Provides heuristic sink detection for common dangerous function patterns.
"""

import re
import logging
from shannon_core.code_index.models import FuncBlock
from shannon_core.code_index.parameter_models import SinkType

logger = logging.getLogger(__name__)

# Patterns for detecting sink types in function source code
_SINK_PATTERNS: list[tuple[re.Pattern, SinkType]] = [
    (re.compile(r'(?:execute|query|cursor\.execute|raw\s*\()', re.IGNORECASE), SinkType.SQL_EXECUTION),
    (re.compile(r'(?:os\.system|subprocess|popen|exec\s*\(|eval\s*\()', re.IGNORECASE), SinkType.COMMAND_EXEC),
    (re.compile(r'(?:pickle\.loads?|yaml\.load|unserialize|deserialize)', re.IGNORECASE), SinkType.DESERIALIZATION),
    (re.compile(r'(?:open\s*\(|write\s*\(|file_put_contents|os\.path\.join.*\.\.)', re.IGNORECASE), SinkType.FILE_WRITE),
    (re.compile(r'(?:render_template|render\s*\(|\.innerHTML|Response\s*\()', re.IGNORECASE), SinkType.TEMPLATE_RENDER),
    (re.compile(r'(?:requests\.(?:get|post)|urllib|http\.Client|fetch\s*\()', re.IGNORECASE), SinkType.HTTP_REQUEST),
    (re.compile(r'(?:logger?\.(?:info|debug|warn|error)|log\.\w+|console\.log)', re.IGNORECASE), SinkType.LOG_WRITE),
]


def classify_sink(block: FuncBlock) -> SinkType:
    """Classify a function block's sink type based on source code patterns.

    Args:
        block: The FuncBlock to classify.

    Returns:
        The detected SinkType, or SinkType.UNKNOWN if no pattern matches.
    """
    source = block.source_code
    for pattern, sink_type in _SINK_PATTERNS:
        if pattern.search(source):
            return sink_type
    return SinkType.UNKNOWN
