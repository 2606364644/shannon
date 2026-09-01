from pydantic import BaseModel, ConfigDict, Field, model_validator


class RepoSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = None
    workspace: str | None = None
    role: str = "backend"
    roles: list[str] = Field(default_factory=list)
    scan_config: str | None = None
    proto_roots: list[str] = Field(default_factory=list)

    @property
    def effective_roles(self) -> set[str]:
        """Compatibility-aware capability set (`role` is the legacy primary label)."""
        return set(self.roles or [self.role])

    @model_validator(mode="after")
    def _check_role_and_inputs(self):
        if self.role not in ("entrypoint", "backend"):
            raise ValueError(f"role must be entrypoint|backend, got {self.role!r}")
        if any(role not in ("entrypoint", "backend") for role in self.roles):
            raise ValueError(f"roles values must be entrypoint|backend, got {self.roles!r}")
        fields_set = self.__pydantic_fields_set__
        if not self.roles:
            self.roles = [self.role]
        elif "roles" in fields_set and "role" not in fields_set:
            self.role = self.roles[0]
        elif self.role not in self.roles:
            raise ValueError("legacy role must be one of roles when both fields are provided")
        if self.path is None and self.workspace is None:
            raise ValueError("repo must have at least one of path or workspace")
        return self


class Relation(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: str = Field(alias="from")
    to: str
    protocol: str = "grpc"

    @model_validator(mode="after")
    def _check_protocol(self):
        if self.protocol not in ("grpc", "http", "graphql"):
            raise ValueError(f"protocol must be grpc|http|graphql, got {self.protocol!r}")
        return self


class CorrelationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    out_workspace: str


class MultiRepoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str | None = None
    repos: dict[str, RepoSpec]
    relations: list[Relation] = Field(default_factory=list)
    correlation: CorrelationConfig

    @model_validator(mode="after")
    def _check_graph(self):
        # 至少一个 entrypoint
        if not any("entrypoint" in r.effective_roles for r in self.repos.values()):
            raise ValueError("at least one repo must have role: entrypoint")
        # relations 引用必须已声明
        names = set(self.repos.keys())
        for rel in self.relations:
            if rel.from_ not in names:
                raise ValueError(f"relation from {rel.from_!r} not in repos")
            if rel.to not in names:
                raise ValueError(f"relation to {rel.to!r} not in repos")
        return self
