from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, model_validator


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
    # 黑盒 = 白盒下游 exploitation-only（阶段 2）：恒复用白盒结果，model_validator
    # （_blackbox_requires_reuse）强制 reuse_whitebox_scan_id 必填 + 禁 source。
    reuse_whitebox_scan_id: str | None = None
    # 黑盒登录配置（结构化 dict，对齐 core Authentication schema：login_type/login_url/credentials
    # /login_flow/success_condition）。scan_manager 内 Authentication.model_validate 校验 + 写 config YAML。
    authentication: dict | None = None
    # correlation 专用
    config_name: str | None = None
    config_content: str | None = None
    save_as: str | None = None

    @model_validator(mode="after")
    def _blackbox_requires_reuse(self) -> "ScanRequest":
        """黑盒 = 白盒下游 exploitation-only（阶段 2）：恒复用白盒结果，禁 standalone/repo。

        黑盒必须有 reuse_whitebox_scan_id，且禁 source（防 API 直传 repo/path 绕过前端）。
        whitebox/correlation 不约束。非法 → ValidationError → FastAPI 422。
        """
        if self.type == "blackbox":
            if not self.reuse_whitebox_scan_id:
                raise ValueError(
                    "blackbox 扫描必须复用白盒结果（reuse_whitebox_scan_id），请先跑白盒扫描"
                )
            if self.source is not None:
                raise ValueError(
                    "blackbox 扫描不支持 source（恒复用白盒结果），请改用 reuse_whitebox_scan_id"
                )
        return self


class ScanAccepted(BaseModel):
    workspace: str
    # T3: 1 ws : N scans 后 POST /api/scan 返回新 scan 的 scan_id（旧前端忽略仍可用）。
    scan_id: str | None = None


class ErrorOut(BaseModel):
    detail: str
