"""统一链接解析（扫描发起页仓库入口整合 A 段，2026-09-03）。

把用户粘贴的链接解析成扫描表单可回填的结构：GitLab MR 链接（``/-/merge_requests/<iid>``
语法，GitLab 特有）→ MrLink + GitLab API 查 source/target 分支；其余 http(s) 仓库
形态 → RepoLink（不调 API，直接匹配工作区仓库）。仓库链接不校验 host——与仓库页
clone 行为一致（任意 git https 远端可 clone），「仅支持 GitLab」约束只对 MR 链接
成立（API + ``-/-`` 语法均 GitLab 特有）。

仓库匹配/clone 落名约定（对齐 GitFetcher.repo_name）：clone 默认落扁平名（URL
最后段），故匹配两级探测——完整 project path（group/repo，用户显式建组时）优先，
回落扁平名（repo）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx

# GitLab MR 路径语法：<project>/-/merge_requests/<iid>（结尾可带尾斜杠；path 以 / 开头）
_MR_PATH_RE = re.compile(r"^/(?P<project>.+?)/-/merge_requests/(?P<iid>\d+)/?$")
# GitHub PR：<owner>/<repo>/pull/<n> —— 语法上与嵌套 group 无法区分，特判给明确错误
_GITHUB_PR_RE = re.compile(r"/pull/\d+/?$")

_API_TIMEOUT = 10.0


@dataclass(frozen=True)
class MrLink:
    scheme: str
    host: str
    project: str   # group/repo 或嵌套 a/b/repo（已剥 .git / merge_requests 段）
    iid: int


@dataclass(frozen=True)
class RepoLink:
    scheme: str
    host: str
    project: str


class UnsupportedLinkError(ValueError):
    """链接形态不识别（非 http(s)、无 project path、GitHub PR 等）。"""


class GitLabApiError(Exception):
    """GitLab API 调用失败。http_status：404=MR 不存在；401=凭据拒绝；0/其他=网络/上游故障。"""

    def __init__(self, http_status: int, message: str) -> None:
        super().__init__(message)
        self.http_status = http_status


def _strip_git_suffix(segment: str) -> str:
    return segment[:-4] if segment.endswith(".git") else segment


def classify_url(url: str) -> MrLink | RepoLink:
    """链接分类：GitLab MR → MrLink；仓库形态 → RepoLink；否则抛 UnsupportedLinkError。"""
    try:
        p = urlparse(url.strip())
    except ValueError as e:
        raise UnsupportedLinkError(f"无法识别的链接：{e}") from e
    if p.scheme not in ("http", "https") or not p.hostname:
        raise UnsupportedLinkError(
            "无法识别的链接，请粘贴 GitLab 仓库或合并请求（MR）链接")
    path = p.path or "/"
    if _GITHUB_PR_RE.search(path):
        raise UnsupportedLinkError("检测到 GitHub PR 链接，暂仅支持 GitLab MR 链接")
    m = _MR_PATH_RE.match(path)
    if m:
        return MrLink(p.scheme, p.hostname, _strip_git_suffix(m.group("project")),
                      int(m.group("iid")))
    project = _strip_git_suffix(path.strip("/"))
    if not project:
        raise UnsupportedLinkError(
            "无法识别的链接，请粘贴 GitLab 仓库或合并请求（MR）链接")
    return RepoLink(p.scheme, p.hostname, project)


async def fetch_merge_request(link: MrLink, token: str) -> dict:
    """查 MR 的 source/target 分支与状态 + 合入把手（merged 改道用）。

    返回 {"source_branch", "target_branch", "state", "merge_commit_sha", "sha",
    "diff_refs"}——后三者是 MR 记录里持久保留的 commit 定位信息（源分支删除后
    仍可查询），merged 改道公式（merged_fallback_commits）的输入。

    失败映射 GitLabApiError：404（MR 不存在/无权限）、401（凭据拒绝）、其余网络/
    上游故障。凭据缺失由调用方前置检查（503 语义，区别于凭据被拒）。
    """
    project_enc = quote(link.project, safe="")
    api = f"{link.scheme}://{link.host}/api/v4/projects/{project_enc}/merge_requests/{link.iid}"
    try:
        async with httpx.AsyncClient(timeout=_API_TIMEOUT) as client:
            resp = await client.get(api, headers={"PRIVATE-TOKEN": token})
    except httpx.HTTPError as e:
        raise GitLabApiError(0, f"GitLab API 网络错误：{e}") from e
    if resp.status_code == 404:
        raise GitLabApiError(404, "MR 不存在或无权限访问")
    if resp.status_code in (401, 403):
        raise GitLabApiError(401, "GitLab 凭据无效或被拒绝")
    if resp.status_code != 200:
        raise GitLabApiError(resp.status_code, f"GitLab API 返回 {resp.status_code}")
    data = resp.json()
    if not data.get("source_branch") or not data.get("target_branch"):
        raise GitLabApiError(0, "GitLab API 返回缺失分支信息")
    return {"source_branch": data["source_branch"], "target_branch": data["target_branch"],
            "state": data.get("state", "opened"),
            "merge_commit_sha": data.get("merge_commit_sha"),
            "sha": data.get("sha"),
            "diff_refs": data.get("diff_refs")}


def merged_fallback_commits(mr: dict) -> tuple[str, str | None] | None:
    """已合并 MR 的增量扫描把手（源分支已删时改道用）。

    返回 (head_commit, base_commit)；base_commit=None 表示 base 交给 worker 按
    head^1（first-parent）解析。两种合并形态统一归结为 commit 对：
    - true merge / squash：merge_commit_sha 即 head；diff = head^1..head 正是 MR
      合入目标分支的全部变更（squash 时 merge_commit_sha 就是 squash commit）。
    - fast-forward：无 merge_commit_sha → MR.sha（FF 后即在目标分支历史上）+
      diff_refs.base_sha（merge-base，同样可达）。
    把手全缺（老 API / 极端）→ None，调用方维持拦截 422。
    """
    mcs = mr.get("merge_commit_sha")
    if mcs:
        return mcs, None
    sha = mr.get("sha")
    base_sha = (mr.get("diff_refs") or {}).get("base_sha")
    if sha and base_sha:
        return sha, base_sha
    return None


async def branch_exists(link: MrLink, branch: str, token: str) -> bool:
    """查 project 分支是否仍存在于远端（merged/closed MR 的源分支常被删）。

    降级语义：网络/凭据/上游异常 → True（放行）。误拦截（明明能扫却被 422 挡死）
    伤害大于漏拦截（放行后走原失败路径，workflow 收尾透传真实原因）。
    """
    project_enc = quote(link.project, safe="")
    branch_enc = quote(branch, safe="")
    api = (f"{link.scheme}://{link.host}/api/v4/projects/{project_enc}"
           f"/repository/branches/{branch_enc}")
    try:
        async with httpx.AsyncClient(timeout=_API_TIMEOUT) as client:
            resp = await client.get(api, headers={"PRIVATE-TOKEN": token})
    except httpx.HTTPError:
        return True
    if resp.status_code == 404:
        return False
    return True
