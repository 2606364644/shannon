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
