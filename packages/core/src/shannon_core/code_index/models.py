from pydantic import BaseModel, ConfigDict
from enum import Enum


class Verdict(str, Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    RECLASSIFIED = "reclassified"


class EntryPointSource(str, Enum):
    CODE_INDEX = "code_index"
    LLM_DISCOVERY = "llm_discovery"


class FuncBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str  # "file_path:function_name:start_line"
    file_path: str
    function_name: str
    start_line: int
    end_line: int
    source_code: str
    parameters: list[str]
    class_name: str | None = None
    decorators: list[str] = []
    language: str  # "python" | "go" | "typescript" | "java" | "php"


class CallEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    caller_id: str  # FuncBlock.id
    callee_name: str  # called function name
    callee_file: str | None = None
    resolved: bool  # whether callee was found in known blocks
    line: int  # line number of the call


class EntryPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    func_block_id: str
    entry_type: str  # "http_route" | "rpc" | "cli" | "message_consumer" | ...
    route: str | None = None
    http_method: str | None = None
    confidence: float
    evidence: str
    needs_llm_review: bool  # True when confidence < 0.8


class CallChain(BaseModel):
    model_config = ConfigDict(frozen=True)

    entry_point_id: str
    path: list[str]  # ordered list of FuncBlock.id
    depth: int
    has_unresolved: bool  # path contains unresolved calls


class CodeIndex(BaseModel):
    repository: str
    language: str
    total_blocks: int
    total_entry_points: int
    total_chains: int
    blocks: list[FuncBlock]
    edges: list[CallEdge]
    entry_points: list[EntryPoint]
    chains: list[CallChain]


class AdjudicatedEntryPoint(BaseModel):
    func_block_id: str
    verdict: Verdict
    entry_type: str
    route: str | None = None
    http_method: str | None = None
    evidence: str
    source: EntryPointSource


class AdjudicationResult(BaseModel):
    repository: str
    language: str
    adjudicated_entry_points: list[AdjudicatedEntryPoint]