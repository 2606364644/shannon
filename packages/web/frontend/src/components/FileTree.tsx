import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, File, FileJson, FileText, Folder } from "lucide-react";
import type { DeliverablesFile } from "../api/types";

interface TreeNode {
  name: string;
  path: string;
  children: Map<string, TreeNode>;
  file?: DeliverablesFile;
}

function insertPath(root: TreeNode, relPath: string, f: DeliverablesFile) {
  const parts = relPath.split("/");
  let cur = root;
  parts.forEach((part, i) => {
    const path = parts.slice(0, i + 1).join("/");
    if (!cur.children.has(part)) cur.children.set(part, { name: part, path, children: new Map() });
    cur = cur.children.get(part)!;
    if (i === parts.length - 1) cur.file = f;
  });
}

// tiering（spec 2026-08-18）：intermediate 文件按 tier 分流进虚拟组（不管其实际
// 路径在新结构 intermediate/ 目录还是旧结构平铺——观感一致）；组内去掉 track
// 首段与 intermediate/ 段（展开即见文件，不重复桶层级）。无 tier 字段（旧后端）
// → 全进主树（兼容）。
const INTERMEDIATE_VIRTUAL_KEY = "__intermediate__";
const _TRACK_SEGMENTS = new Set(["whitebox", "blackbox", "combined"]);

function buildTree(files: DeliverablesFile[]): TreeNode {
  const root: TreeNode = { name: "", path: "", children: new Map() };
  let interNode: TreeNode | null = null;
  for (const f of files) {
    if (f.tier === "intermediate") {
      interNode ??= { name: INTERMEDIATE_VIRTUAL_KEY, path: INTERMEDIATE_VIRTUAL_KEY, children: new Map() };
      const rel = f.path
        .split("/")
        .filter((seg, i, arr) => seg !== "intermediate"
          && !(i === 0 && arr.length > 1 && _TRACK_SEGMENTS.has(seg)))
        .join("/");
      insertPath(interNode, rel, f);
    } else {
      insertPath(root, f.path, f);
    }
  }
  if (interNode) root.children.set(INTERMEDIATE_VIRTUAL_KEY, interNode);
  return root;
}

// 顶层 track 目录 → 本地化友好名（组合扫描三桶语义融入一棵树）；其余目录显示原名。
const TRACK_LABEL_KEYS: Record<string, string> = {
  whitebox: "fileTree.trackWhitebox",
  blackbox: "fileTree.trackBlackbox",
  combined: "fileTree.trackCombined",
  // tiering（spec 2026-08-18）：tier=intermediate 文件统一收进该虚拟组（默认折叠）
  __intermediate__: "fileTree.intermediate",
};

function fileIcon(kind: DeliverablesFile["kind"]) {
  const cls = "size-3.5 shrink-0 text-muted-foreground";
  if (kind === "md") return <FileText className={cls} aria-hidden />;
  if (kind === "other") return <File className={cls} aria-hidden />;
  return <FileJson className={cls} aria-hidden />;
}

export function FileTree({
  files,
  onSelect,
  selectedPath,
}: {
  files: DeliverablesFile[];
  onSelect: (f: DeliverablesFile) => void;
  selectedPath?: string | null;
}) {
  const root = buildTree(files);
  return (
    <ul className="list-none p-0 text-sm">
      {Array.from(root.children.values()).map((n) => (
        <NodeView key={n.path} node={n} depth={0} onSelect={onSelect} selectedPath={selectedPath ?? null} />
      ))}
    </ul>
  );
}

function NodeView({
  node,
  depth,
  onSelect,
  selectedPath,
}: {
  node: TreeNode;
  depth: number;
  onSelect: (f: DeliverablesFile) => void;
  selectedPath: string | null;
}) {
  // 中间产物虚拟组默认折叠（tiering：交付物优先，排障时可展开）
  const [open, setOpen] = useState(depth < 1 && node.name !== INTERMEDIATE_VIRTUAL_KEY);
  const { t } = useTranslation();
  const isDir = node.children.size > 0;
  const selected = !isDir && selectedPath === node.path;
  const trackLabelKey = depth === 0 && isDir ? TRACK_LABEL_KEYS[node.name] : undefined;
  return (
    <li>
      <div style={{ paddingLeft: depth * 14 }} className="py-px">
        {isDir ? (
          <button
            className="flex w-full items-center gap-1 bg-transparent p-0 font-inherit text-foreground hover:text-primary"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
          >
            <span className="text-muted-foreground" aria-hidden>
              {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
            </span>
            <Folder className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
            <span className="truncate">{trackLabelKey ? t(trackLabelKey) : node.name}</span>
          </button>
        ) : (
          <button
            className={`flex w-full items-center gap-1 rounded-sm p-0 text-left font-mono hover:text-primary ${
              selected ? "bg-accent text-accent-foreground" : "bg-transparent"
            }`}
            aria-current={selected ? "true" : undefined}
            onClick={() => onSelect(node.file!)}
          >
            {fileIcon(node.file!.kind)}
            <span className="truncate">{node.name}</span>
            {node.file?.kind === "empty_json" && <span className="text-xs text-muted-foreground">{t("fileTree.empty")}</span>}
            {node.file?.kind === "big_json" && <span className="text-xs text-muted-foreground">{t("fileTree.large")}</span>}
          </button>
        )}
      </div>
      {isDir && open && Array.from(node.children.values()).map((c) => (
        <ul key={c.path} className="list-none p-0">
          <NodeView node={c} depth={depth + 1} onSelect={onSelect} selectedPath={selectedPath} />
        </ul>
      ))}
    </li>
  );
}
