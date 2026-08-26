/** 锚点精准定位（2026-08-26 报告目录跳转修复）：ScanDetail 页有**双层 sticky 头**——
 *  TopBar(h-12=48px) + 「进度概览 + scan tabs」sticky 块(top-12，高度随进度内容变，
 *  实测 ~100px+)，合计遮蔽带 ≈150px，远超旧锚点 scroll-mt-20(80px) 预留——
 *  scrollIntoView(block:"start") 把卡头 ID/标题行留在遮蔽带里，症状即「点目录跳转
 *  后定位到漏洞标题下方，看不到标题」。修复立场：**不猜固定值**（sticky 块高度随
 *  主题/语言/进度概览内容变化），点击时刻运行时量取各 sticky 块 rect.bottom 取
 *  max，window.scrollTo 精确落点让目标完整露出在遮蔽带下方；并复用 .dataflow-flash
 *  coral 描边闪烁（tokens.css，与 dataflow 目录/?tree= 深链同一「定位确认」语言）。
 *  量取发生在点击时刻：sticky 未贴顶（页面还在顶部）时 rect.bottom 是文档流位置
 *  ≥ 贴顶值 → 多留空隙是安全方向（宁多勿遮）。 */
const FLASH_CLEANUP_MS = 2000;
/** 闪烁清理定时器（单例：新定位自动接管，旧定时器不再对新目标生效）。 */
let flashTimer: number | undefined;

/** 遮蔽带量取选择器：TopBar + ScanDetail sticky 头（外层 sticky div，含进度概览+tabs）。
 *  元素缺席（非 scan 页 / TopBar 外测试环境）按 0 计，互不影响。 */
const STICKY_SELECTORS = ['[data-testid="topbar"]', '[data-testid="scan-sticky-header"]'];

/** 量取当前 sticky 头遮蔽带下沿（各块 rect.bottom 最大值）+ 8px 呼吸余量。 */
export function stickyHeaderOffset(): number {
  let bottom = 0;
  for (const sel of STICKY_SELECTORS) {
    const el = document.querySelector(sel);
    if (el) bottom = Math.max(bottom, el.getBoundingClientRect().bottom);
  }
  return bottom + 8;
}

/**
 * 定位锚点：平滑滚动到目标（落点 = 目标顶 − sticky 遮蔽带）+ coral 描边闪烁后渐隐。
 * 报告目录 / 执行摘要 top_risks / dataflow 目录与 ?tree= 深链共用。
 * 找不到目标（深链失效 / 数据未含该条目）返回 false，静默不报错。
 * 单一 active-target 语义：触发新定位前清掉所有旧目标描边（连点多目标不残留累积）。
 * @param resolve 查找器（默认按元素 id；dataflow 传 data-tree-id 等属性查询）
 */
export function focusAnchor(id: string, resolve?: (id: string) => Element | null): boolean {
  const el = resolve ? resolve(id) : document.getElementById(id);
  if (!el) return false;
  // 清旧：摘掉所有仍带描边的目标 + 作废旧清理定时器
  for (const prev of Array.from(document.querySelectorAll(".dataflow-flash"))) {
    prev.classList.remove("dataflow-flash");
  }
  if (flashTimer !== undefined) {
    window.clearTimeout(flashTimer);
    flashTimer = undefined;
  }
  const top = Math.max(0, el.getBoundingClientRect().top + window.scrollY - stickyHeaderOffset());
  window.scrollTo({ top, behavior: "smooth" });
  // 重启动画：先摘 class 再强制 reflow 再加回（连续定位同一目标也能重新闪烁）
  el.classList.remove("dataflow-flash");
  void (el as HTMLElement).offsetWidth;
  el.classList.add("dataflow-flash");
  // 动画结束自清理（jsdom 无动画事件，用与动画时长对齐的 timer 兜底）
  flashTimer = window.setTimeout(() => {
    el.classList.remove("dataflow-flash");
    flashTimer = undefined;
  }, FLASH_CLEANUP_MS);
  return true;
}
