// i18next-parser 配置 —— 静态提取 src/**/*.{ts,tsx} 中的 t("key") 调用。
// 用法：npm run i18n:scan （默认写 src/locales/$LOCALE.json）
// 注意：默认 output 会重写已有 locale 文件，CI/比对场景请用 --output 指向临时目录。
// package.json 为 ESM ("type":"module")，故用 export default。
export default {
  createOldCatalogs: false,
  input: ["src/**/*.{ts,tsx}"],
  output: "src/locales/$LOCALE.json",
  locales: ["zh", "en"],
  defaultNamespace: "translation",
  namespaceSeparator: false,
  keySeparator: ".",
  verbose: true,
  // 只提取、不覆盖已有翻译值；en 留空串由人工/校验补齐
  defaultValue: (lng, _ns, key) => (lng === "zh" ? key : ""),
};
