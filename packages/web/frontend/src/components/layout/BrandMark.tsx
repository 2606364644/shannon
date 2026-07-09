/**
 * 品牌图标 —— 图盾 Hex-Graph。
 *
 * 设计语义：六边形图盾（传承原 hex 视觉）内嵌一张微型 taint 图——
 * 两个 source 节点（描边）经有向边汇聚到一个 sink 节点（实心高亮），
 * 呼应平台「在代码图里追 source→sink 漏洞链」的核心工作。
 *
 * 颜色靠 currentColor 继承：外层套 text-cyan 等语义色即可换色。
 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      aria-hidden="true"
      role="presentation"
    >
      {/* 六边形图盾（容器，最淡） */}
      <path
        d="M12 2.2 L20.49 7.1 L20.49 16.9 L12 21.8 L3.51 16.9 L3.51 7.1 Z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
        opacity="0.42"
      />
      {/* taint 有向边：source A/B → sink（起止贴合节点边缘，不穿圆） */}
      <path
        d="M8.88 10.71 L10.88 13.46 M15.12 10.71 L13.12 13.46"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinecap="round"
        opacity="0.78"
      />
      {/* source 节点（描边） */}
      <circle cx="8" cy="9.5" r="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="16" cy="9.5" r="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      {/* sink 节点（实心高亮，视觉焦点） */}
      <circle cx="12" cy="15" r="1.9" fill="currentColor" />
    </svg>
  );
}
