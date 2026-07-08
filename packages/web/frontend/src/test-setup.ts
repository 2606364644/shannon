// 必须最先：装好 localStorage polyfill，detector 才能在 @/i18n init 时检测到它可用
import "./test-setup-ls";
import "@/i18n";
import "@testing-library/jest-dom/vitest";

// jsdom 缺 matchMedia，主题库 / 减少动效检测依赖它
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList;
}

// localStorage polyfill 已提取到 ./test-setup-ls（必须在 @/i18n 之前执行）

// jsdom 未实现 Element.prototype.scrollIntoView；Radix Select/Combobox 打开时调用，缺则抛错导致整个组件树卸载
if (typeof Element.prototype.scrollIntoView !== "function") {
  Element.prototype.scrollIntoView = () => {};
}
