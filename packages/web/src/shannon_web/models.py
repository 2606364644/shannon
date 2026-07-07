from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel


class PathSource(BaseModel):
    kind: Literal["path"]
    value: str


class RepoSource(BaseModel):
    kind: Literal["repo"]
    value: str  # 仓库名（可为 group/repo 或扁平 repo，对应 repos_dir 下相对路径）


Source = Union[PathSource, RepoSource]


class ScanRequest(BaseModel):
    type: Literal["whitebox", "blackbox", "correlation"]
    source: Source | None = None
    url: str | None = None
    workspace: str | None = None
    reuse_latest: bool = False
    # correlation 专用
    config_name: str | None = None
    config_content: str | None = None
    save_as: str | None = None


class ScanAccepted(BaseModel):
    workspace: str


class ErrorOut(BaseModel):
    detail: str
