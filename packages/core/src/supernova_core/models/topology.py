"""Pydantic contracts for read-only cross-repo topology discovery.

The discovery stage is intentionally independent from vulnerability analysis: these
objects describe navigation facts, candidate service capabilities/edges, evidence
quality, and normalized AI output.  They never model findings or exploitability.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TOPOLOGY_PROTOCOLS: tuple[str, ...] = ("grpc", "http", "graphql")
_RE_SAFE_REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

RepoRole = Literal["entrypoint", "backend"]
Confidence = Literal["high", "medium", "low"]


class TopologyAnalysisRequest(BaseModel):
    """A request to analyze repositories that already belong to one workspace."""

    model_config = ConfigDict(extra="forbid")
    repos: list[str]

    @field_validator("repos")
    @classmethod
    def _normalize_repos(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for repo in value:
            if not _RE_SAFE_REPO_NAME.fullmatch(repo) or repo.endswith(("/", ".")):
                raise ValueError(f"invalid repository name: {repo!r}")
            if repo not in seen:
                seen.add(repo)
                normalized.append(repo)
        return sorted(normalized)


class TopologyUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    cost_currency: str = "USD"
    model: str | None = None
    turns: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class TopologyEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    repo: str
    file: str
    line: int | None = None
    snippet: str = ""
    kind: Literal["client", "handler", "config", "capability"] = "client"
    valid: bool | None = None
    validation_errors: list[str] = Field(default_factory=list)


class TopologyNodeCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: RepoRole
    confidence: Confidence = "medium"
    evidence: list[TopologyEvidence] = Field(default_factory=list)


class TopologyNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str
    roles: list[RepoRole] = Field(default_factory=list)
    capabilities: list[TopologyNodeCapability] = Field(default_factory=list)

    @property
    def legacy_role(self) -> RepoRole | None:
        return self.roles[0] if self.roles else None


class TopologyCandidateEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)
    from_: str = Field(validation_alias="from", serialization_alias="from")
    to: str
    protocol: str
    confidence: Confidence = "medium"
    service: str | None = None
    method: str | None = None
    client_evidence: list[TopologyEvidence] = Field(default_factory=list)
    handler_evidence: list[TopologyEvidence] = Field(default_factory=list)


class TopologyUncertainClue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str
    message: str
    protocol_hint: str | None = None
    evidence: list[TopologyEvidence] = Field(default_factory=list)


class TopologyCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str
    complete: bool = False
    reason: str = ""


class TopologyAnalysisResult(BaseModel):
    """Raw structured agent output before deterministic normalization."""
    model_config = ConfigDict(extra="allow")
    nodes: list[TopologyNode] = Field(default_factory=list)
    edges: list[TopologyCandidateEdge] = Field(default_factory=list)
    uncertain: list[TopologyUncertainClue] = Field(default_factory=list)
    coverage: list[TopologyCoverage] = Field(default_factory=list)
    usage: TopologyUsage | None = None


class TopologyInvalidItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Literal[
        "unknown_node", "self_loop", "invalid_protocol", "invalid_evidence",
        "duplicate_edge", "malformed_output",
    ]
    message: str
    raw: dict[str, Any] = Field(default_factory=dict)


class NormalizedTopologyResult(TopologyAnalysisResult):
    """Validated candidate graph; every field remains advisory until user confirmation."""
    model_config = ConfigDict(extra="forbid")
    invalid: list[TopologyInvalidItem] = Field(default_factory=list)
    raw: TopologyAnalysisResult | None = None


class NavigationClue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    path: str
    line: int
    snippet: str
    value: str | None = None


class RepositoryNavigationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str
    path: str
    language: str | None = None
    package_name: str | None = None
    frameworks: list[str] = Field(default_factory=list)
    service_clues: list[NavigationClue] = Field(default_factory=list)
    client_clues: list[NavigationClue] = Field(default_factory=list)
    config_clues: list[NavigationClue] = Field(default_factory=list)
    scanned_files: int = 0
    truncated: bool = False

    def all_clues(self) -> list[NavigationClue]:
        return [*self.service_clues, *self.client_clues, *self.config_clues]


class NavigationManifestLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_files: int = 1200
    max_file_bytes: int = 256 * 1024
    max_output_chars: int = 64 * 1024
    max_clues_per_repo: int = 120
    max_snippet_chars: int = 240

    @field_validator(
        "max_files", "max_file_bytes", "max_output_chars",
        "max_clues_per_repo", "max_snippet_chars",
    )
    @classmethod
    def _positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("manifest limits must be positive")
        return value


class NavigationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: str = "topology-navigation-v1"
    repositories: list[str] = Field(default_factory=list)
    by_repo: dict[str, RepositoryNavigationManifest] = Field(default_factory=dict)
    limits: NavigationManifestLimits = Field(default_factory=NavigationManifestLimits)
    approximate_size: int = 0
    truncated: bool = False


class TopologyRepoFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    git_head: str | None = None
    dirty: bool = False
    fallback_hash: str | None = None


class TopologyFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: str = "cross-repo-topology-v1"
    repos: dict[str, TopologyRepoFingerprint] = Field(default_factory=dict)
    value: str


__all__ = [name for name in dir() if name.startswith(("Topology", "Navigation", "Repo"))]
