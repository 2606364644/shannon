/**
 * 复制文本到剪贴板（双路径），返回是否成功（调用方据此切反馈 / toast 报错）。
 *
 * 真根因背景（2026-08-27 修复「报告代码块复制不生效」）：经 http://内网IP:7878 这类
 * **非安全上下文**访问 web 时（Clipboard API 只在 HTTPS / localhost 暴露），
 * `navigator.clipboard === undefined`——`navigator.clipboard?.writeText(v)` 会静默
 * 无操作且不产生 rejection，按钮却照样显示 ✓/Check，形成「假成功」。这里对不可用 /
 * 失败（权限拒绝）都 fallback 到临时 textarea + document.execCommand("copy")
 * （deprecated 但所有主流浏览器在非安全上下文仍可用；须在用户手势内同步调用）。
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // writeText reject（如权限拒绝）→ 落 legacy fallback 再试
  }
  return legacyCopy(text);
}

/** execCommand 路径：临时 textarea 挂视口外（不触发滚动/闪烁）→ 选中 → copy → 即删。
 *  focus + setSelectionRange 兼顾 iOS Safari 对 textarea select() 的已知怪癖。 */
function legacyCopy(text: string): boolean {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "0";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  ta.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(ta);
  return ok;
}
