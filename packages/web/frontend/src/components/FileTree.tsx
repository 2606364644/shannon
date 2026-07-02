import { useState } from "react";
import type { DeliverablesFile } from "../api/types";

interface TreeNode {
  name: string;
  path: string;
  children: Map<string, TreeNode>;
  file?: DeliverablesFile;
}

function buildTree(files: DeliverablesFile[]): TreeNode {
  const root: TreeNode = { name: "", path: "", children: new Map() };
  for (const f of files) {
    const parts = f.path.split("/");
    let cur = root;
    parts.forEach((part, i) => {
      const path = parts.slice(0, i + 1).join("/");
      if (!cur.children.has(part)) cur.children.set(part, { name: part, path, children: new Map() });
      cur = cur.children.get(part)!;
      if (i === parts.length - 1) cur.file = f;
    });
  }
  return root;
}

export function FileTree({
  files,
  onSelect,
}: {
  files: DeliverablesFile[];
  onSelect: (f: DeliverablesFile) => void;
}) {
  const root = buildTree(files);
  return (
    <ul className="file-tree">
      {Array.from(root.children.values()).map((n) => (
        <NodeView key={n.path} node={n} depth={0} onSelect={onSelect} />
      ))}
    </ul>
  );
}

function NodeView({
  node,
  depth,
  onSelect,
}: {
  node: TreeNode;
  depth: number;
  onSelect: (f: DeliverablesFile) => void;
}) {
  const [open, setOpen] = useState(depth < 1);
  const isDir = node.children.size > 0;
  return (
    <li>
      <div style={{ paddingLeft: depth * 14 }} className="ft-row">
        {isDir ? (
          <button className="ft-toggle" onClick={() => setOpen((o) => !o)}>
            <span className="ft-caret">{open ? "▾" : "▸"}</span> 📂{" "}
            <span className="ft-name">{node.name}</span>
          </button>
        ) : (
          <span className="ft-file mono" onClick={() => onSelect(node.file!)}>
            📄 <span className="ft-name">{node.name}</span>
            {node.file?.kind === "empty_json" && <span className="trace"> （空）</span>}
            {node.file?.kind === "big_json" && <span className="trace"> （大）</span>}
          </span>
        )}
      </div>
      {isDir && open && Array.from(node.children.values()).map((c) => (
        <ul key={c.path}>
          <NodeView node={c} depth={depth + 1} onSelect={onSelect} />
        </ul>
      ))}
    </li>
  );
}
