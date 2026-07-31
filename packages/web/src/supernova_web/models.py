from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel


class PathSource(BaseModel):
    kind: Literal["path"]
    value: str


class RepoSource(BaseModel):
    kind: Literal["repo"]
    value: str  # 仓库名（可为 group/repo 或扁平 repo，对应 repos_dir 下相对路径）


# Web 入口已收窄为「工作区已下载仓库」——前端不再发 path source，但保留 PathSource 以兼容
# 可能的旧调用方 / CLI shim；scan_manager._resolve_inputs 仍按 kind 分流。
Source = Union[PathSource, RepoSource]


class ScanRequest(BaseModel):
    type: Literal["whitebox", "blackbox", "correlation"]
    source: Source | None = None
    url: str | None = None
    workspace: str | None = None
    reuse_latest: bool = False
    # 黑盒「复用白盒结果」：要复用的白盒 scan_id（工作区内某 whitebox scan）。
    # 黑盒 C1 提交仍为 Phase C stub（scan_manager.start NotImplementedError），此处仅落契约。
    reuse_whitebox_scan_id: str | None = None
    # correlation 专用
    config_name: str | None = None
    config_content: str | None = None
    save_as: str | None = None


class ScanAccepted(BaseModel):
    workspace: str
    # T3: 1 ws : N scans 后 POST /api/scan 返回新 scan 的 scan_id（旧前端忽略仍可用）。
    scan_id: str | None = None


class ErrorOut(BaseModel):
    detail: str
