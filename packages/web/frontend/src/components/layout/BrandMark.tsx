/**
 * 品牌图标 -- 节点流（Claude 风重做，2026-07-10）。
 *
 * 设计语义：两张 source 节点（描边圆）经有向边汇聚到一个 sink 节点（coral 实心高亮），
 * 呼应平台「在代码图里追 source->sink 漏洞链」的核心工作。
 * 去掉旧版六边形工程外壳，圆润克制，sink 用 coral（--primary）做视觉焦点，
 * 与 Claude 风暖纸张 + coral 主色统一。
 *
 * source/连线靠 currentColor 继承（外层套 text-cyan 等语义色即可换色）；
 * sink 固定 coral，经 var(--primary) 随主题切换。
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
      {/* source 节点（描边） */}
      <circle cx="7" cy="8" r="2" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="17" cy="8" r="2" stroke="currentColor" strokeWidth="1.6" />
      {/* taint 有向边：source A/B -> sink */}
      <path
        d="M8.5 9.6 L11 14 M15.5 9.6 L13 14"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        opacity="0.65"
      />
      {/* sink 节点（coral 实心高亮，视觉焦点） */}
      <circle cx="12" cy="16" r="2.5" style={{ fill: "hsl(var(--primary))" }} />
    </svg>
  );
}
