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

// vitest 2.x + jsdom 24：window.localStorage 是不完整对象（缺 clear/getItem/setItem），polyfill 成 in-memory Storage
if (typeof window.localStorage?.clear !== "function") {
  const store = new Map<string, string>();
  const polyfill: Storage = {
    getItem: (k) => store.get(k) ?? null,
    setItem: (k, v) => { store.set(k, String(v)); },
    removeItem: (k) => { store.delete(k); },
    clear: () => { store.clear(); },
    key: (i) => [...store.keys()][i] ?? null,
    get length() { return store.size; },
  };
  Object.defineProperty(window, "localStorage", { value: polyfill, configurable: true });
}

// jsdom 未实现 Element.prototype.scrollIntoView；Radix Select/Combobox 打开时调用，缺则抛错导致整个组件树卸载
if (typeof Element.prototype.scrollIntoView !== "function") {
  Element.prototype.scrollIntoView = () => {};
}
