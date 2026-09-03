"""git unified diff（-U3）→ DiffManifest 解析（spec §4.1）。

行号坐标系：added 行号 = head 侧（与 head 索引 SinkCallSite.line 同系，零映射）；
removed 行号 = base 侧（RemovedProtection 定位时经 hunk 区间映射转 head 侧）。
"""

from pydantic import BaseModel


class DiffLine(BaseModel):
    text: str
    head_line_no: int | None = None   # added 行的 head 侧行号
    base_line_no: int | None = None   # removed 行的 base 侧行号


class DiffHunk(BaseModel):
    file_path: str                     # 新侧路径
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    added: list[DiffLine] = []
    removed: list[DiffLine] = []
    is_new_file: bool = False
    is_deleted_file: bool = False
    rename_from: str | None = None     # rename 时旧路径


class DiffStats(BaseModel):
    files: int = 0
    insertions: int = 0
    deletions: int = 0


class DiffManifest(BaseModel):
    base_commit: str
    head_commit: str
    hunks: list[DiffHunk] = []
    stats: DiffStats = DiffStats()

    def resolve_head_path(self, path: str) -> str:
        """任意一侧路径 → 归一到 head 侧（rename 旧路径映射到新路径）。"""
        for h in self.hunks:
            if h.rename_from and path == h.rename_from:
                return h.file_path
        return path

    def added_line_set(self, file_path: str) -> set[int]:
        """文件的新增行号集合（head 侧坐标系，与索引行号直接比对）。"""
        path = self.resolve_head_path(file_path)
        lines: set[int] = set()
        for h in self.hunks:
            if h.file_path == path or h.rename_from == file_path:
                lines.update(l.head_line_no for l in h.added if l.head_line_no is not None)
        return lines


def parse_unified_diff(diff_text: str, base_commit: str, head_commit: str) -> DiffManifest:
    """解析 `git diff -U3` 输出。

    只关心行号坐标系与内容行；上下文行仅用于推进两侧行号计数。
    """
    hunks: list[DiffHunk] = []
    current: DiffHunk | None = None
    current_path = ""
    seg_new = seg_deleted = False
    seg_rename_from: str | None = None
    seg_hunks: list[DiffHunk] = []          # 当前文件段的 hunks
    old_no = new_no = 0

    def _flush_segment():
        # 纯 rename（无 @@ hunk）也要产出零内容 hunk 携带 rename 映射
        if not seg_hunks and seg_rename_from:
            seg_hunks.append(DiffHunk(file_path=current_path, old_start=0, old_lines=0,
                                      new_start=0, new_lines=0, rename_from=seg_rename_from))
        for h in seg_hunks:
            h.rename_from = seg_rename_from
        hunks.extend(seg_hunks)
        seg_hunks.clear()

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            if current_path:  # 非首个段
                _flush_segment()
            current = None  # 新文件段，重置
            seg_new = seg_deleted = False
            seg_rename_from = None
            # `diff --git a/X b/Y` → 新侧路径 Y（strip 引号形态后续按需支持）
            tail = raw[len("diff --git "):]
            b_side = tail.rsplit(" b/", 1)[-1] if " b/" in tail else tail
            current_path = b_side.strip('"')
            continue
        if raw.startswith("rename from "):
            seg_rename_from = raw[len("rename from "):].strip('"')
            continue
        if raw.startswith("new file mode"):
            seg_new = True
            continue
        if raw.startswith("deleted file mode"):
            seg_deleted = True
            continue
        if raw.startswith("--- ") or raw.startswith("+++ "):
            continue  # 文件路径经 @@ 前的 hunk 载体携带；basic 场景路径由 diff --git 行取
        if raw.startswith("@@"):
            # @@ -old_start,old_lines +new_start,new_lines @@ ...
            header = raw[raw.index("-"):].split("@@")[0].strip()
            old_part, new_part = header.split()[:2]
            old_start = int(old_part.split(",")[0].lstrip("-+"))
            new_start = int(new_part.split(",")[0].lstrip("-+"))
            old_no, new_no = old_start, new_start
            current = DiffHunk(
                file_path=current_path, old_start=old_start,
                old_lines=int(old_part.split(",")[1]) if "," in old_part else 1,
                new_start=new_start,
                new_lines=int(new_part.split(",")[1]) if "," in new_part else 1,
                is_new_file=seg_new, is_deleted_file=seg_deleted,
            )
            seg_hunks.append(current)
            continue
        if current is None:
            continue
        if raw.startswith("+"):
            current.added.append(DiffLine(text=raw[1:], head_line_no=new_no))
            new_no += 1
        elif raw.startswith("-"):
            current.removed.append(DiffLine(text=raw[1:], base_line_no=old_no))
            old_no += 1
        elif raw.startswith("\\"):
            continue  # "\ No newline at end of file"
        else:
            old_no += 1
            new_no += 1

    if current_path:
        _flush_segment()

    return DiffManifest(
        base_commit=base_commit, head_commit=head_commit, hunks=hunks,
        stats=DiffStats(files=len({h.file_path for h in hunks if h.file_path}),
                        insertions=sum(len(h.added) for h in hunks),
                        deletions=sum(len(h.removed) for h in hunks)),
    )
