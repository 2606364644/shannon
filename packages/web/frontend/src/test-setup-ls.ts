// vitest 2.x + jsdom 24：window.localStorage 是不完整对象（缺 clear/getItem/setItem），polyfill 成 in-memory Storage
//
// ⚠️ 必须在 `@/i18n` 之前 import：i18next-browser-languagedetector 在 i18n.init() 时
//    一次性 memoize `localStorageAvailable()`（try setItem / catch，永不重试）。
//    若本 polyfill 尚未挂载，jsdom localStorage 后端未就绪 → setItem 抛 → detector 永久
//    判定 localStorage 不可用 → `changeLanguage` 的 `caches:["localStorage"]` 持久化变 no-op
//    → 语言切换不写 localStorage。
//    ESM import 按源码顺序求值，故 test-setup.ts 顶部需 `import "./test-setup-ls"` 先于 `import "@/i18n"`。
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
