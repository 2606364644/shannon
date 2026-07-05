import { useEffect, useRef } from "react";
import Editor from "@monaco-editor/react";
import { load as yamlLoad } from "js-yaml";

// 约定：onError(msg) —— msg 非空=有错，空串=恢复合法。
// 初始即合法时不调 onError；从错误恢复到合法时调 onError("")。
// ScanNewPage（Task 10）据此禁用/启用「直接运行」按钮。
export function YamlEditor({
  value,
  onChange,
  onError,
}: {
  value: string;
  onChange: (v: string) => void;
  onError: (msg: string) => void;
}) {
  const hadErrorRef = useRef(false);

  useEffect(() => {
    let parsed = true;
    try {
      yamlLoad(value);
    } catch (e) {
      parsed = false;
      hadErrorRef.current = true;
      onError((e as Error).message);
    }
    if (parsed) {
      if (hadErrorRef.current) {
        hadErrorRef.current = false;
        onError("");
      }
    }
  }, [value, onError]);

  return (
    <div className="border border-border rounded-md overflow-hidden">
      <Editor
        height="320px"
        language="yaml"
        theme="vs-dark"
        value={value}
        onChange={(v) => onChange(v ?? "")}
        options={{ minimap: { enabled: false }, fontSize: 13, scrollBeyondLastLine: false }}
      />
    </div>
  );
}
