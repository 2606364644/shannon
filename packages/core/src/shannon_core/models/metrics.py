from pydantic import BaseModel

class AgentMetrics(BaseModel):
    duration_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    model: str | None = None
    structured_output: dict | None = None
    stop_reason: str | None = None

class SessionMetadata(BaseModel):
    model_config = {"extra": "allow"}
    id: str
    web_url: str | None = None
    repo_path: str | None = None
    output_path: str | None = None
