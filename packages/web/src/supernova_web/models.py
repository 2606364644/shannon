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
    # /login_flow）。scan_manager 内 Authentication.model_validate 校验 + 写 config YAML。
    authentication: dict | None = None
    # inline 多角色附加账号（2026-08-07）：与 authentication 同存，scan_manager 展开成 accounts[]。
    # 每条 {role, username, password, totp_secret?}；validator 保证仅 authentication 存在时合法。
    auth_accounts: list[dict] | None = None
    # 黑盒选已保存档案/角色(与 inline authentication 二选一):scan_manager 展开成 credentials。
    # 三模式（model_validator _auth_profile_xor_inline 保证互斥）：
    #   - profile_id 单独          = 全角色模式（展开档案所有 credentials → accounts[]，多身份验证）；
    #   - profile_id + cred_ids[]  = 子集模式（展开选中的 credentials → accounts[]，默认前端全选）；
    #   - profile_id + cred_id     = 单角色模式（旧契约，向后兼容；展开该 credential → 单 authentication）。
    auth_profile_id: str | None = None
    auth_credential_id: str | None = None
    # 多角色子集（2026-08-06）：profile 模式选多个角色，空=全选该档案所有角色。
    auth_credential_ids: list[str] | None = None
    # correlation 专用
    config_name: str | None = None
    config_content: str | None = None
    save_as: str | None = None
    # HOST 档案（Phase 2，2026-08-12）：与认证字段独立（HOST 可与任意 auth 模式组合）。
    # 两互斥来源（model_validator _host_profile_xor_url 保证互斥）：
    #   - host_profile_id = 选已保存 HOST 档案（scan_manager 取 store 解析 → mappings）；
    #   - host_url        = 填 /etc/hosts GET 链接（扫描启动时拉取 → mappings，结束可选 upsert）。
    # 都不填 = 不启用 HOST 代理（向后兼容，既有扫描字节不变）。
    host_profile_id: str | None = None   # 选 HOST 档案
    host_url: str | None = None          # 或填 GET 链接（扫描时拉取）

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

    @model_validator(mode="after")
    def _auth_profile_xor_inline(self) -> "ScanRequest":
        """blackbox 登录模式互斥校验（2026-08-06 多角色子集）。

        - profile_id 单独          = 全角色模式（scan_manager 展开所有 credentials → accounts[]）；
        - profile_id + cred_ids[]  = 子集模式（展开选中的 credentials → accounts[]，默认前端全选）；
        - profile_id + cred_id     = 单角色模式（旧契约，向后兼容）；
        - inline authentication    = 内联登录（现状）；
        - profile_id + inline      = 非法（互斥）；
        - cred_id 或 cred_ids 无 profile_id = 非法（必须依附 profile）。
        """
        if self.type == "blackbox":
            has_profile = self.auth_profile_id is not None
            has_cred = self.auth_credential_id is not None
            has_cred_ids = self.auth_credential_ids is not None
            has_inline = self.authentication is not None
            has_accounts = self.auth_accounts is not None
            # auth_accounts 属 inline 侧：仅与 authentication 同存（inline 多角色附加账号）。
            if has_accounts and not has_inline:
                raise ValueError("auth_accounts 必须与 authentication 同时提供（内联多角色附加账号）")
            if (has_profile or has_cred or has_cred_ids) and (has_inline or has_accounts):
                raise ValueError("blackbox 登录:不能同时指定认证档案与内联登录配置")
            if (has_cred or has_cred_ids) and not has_profile:
                raise ValueError("选认证档案角色时必须同时指定 auth_profile_id")
        return self

    @model_validator(mode="after")
    def _host_profile_xor_url(self) -> "ScanRequest":
        """HOST 字段互斥校验（2026-08-12 Phase 2）。

        - host_profile_id + host_url 同时填 = 非法（互斥，避免双源冲突）；
        - 单填一个 = 合法；都不填 = 合法（向后兼容，无 HOST 代理）。
        与认证字段完全独立（HOST 可与任意 auth 模式组合：profile/inline/无 auth）。
        仅对 blackbox 生效（HOST 代理只作用于黑盒扫描；whitebox/correlation 即便误填也忽略）。
        """
        if self.type == "blackbox":
            if self.host_profile_id is not None and self.host_url is not None:
                raise ValueError(
                    "host_profile_id 与 host_url 互斥，不能同时指定（HOST 档案二选一）"
                )
        return self


class ScanAccepted(BaseModel):
    workspace: str
    # T3: 1 ws : N scans 后 POST /api/scan 返回新 scan 的 scan_id（旧前端忽略仍可用）。
    scan_id: str | None = None


class ErrorOut(BaseModel):
    detail: str
