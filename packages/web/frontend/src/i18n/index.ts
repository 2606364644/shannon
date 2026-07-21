import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import zh from "../locales/zh.json";
import en from "../locales/en.json";

void i18n
  .use(initReactI18next)
  .use(LanguageDetector)
  .init({
    resources: {
      zh: { translation: zh },
      en: { translation: en },
    },
    fallbackLng: "zh",
    supportedLngs: ["zh", "en"],
    load: "languageOnly",
    interpolation: { escapeValue: false },
    react: { useSuspense: false },
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: "supernova.lang",
      // languagedetector v8 移除了 cacheUserLanguage;caches 指定持久化后端,
      // ["localStorage"] 与 lookupLocalStorage 对称读写,等价于旧版 cacheUserLanguage:true。
      caches: ["localStorage"],
    },
  });

export default i18n;
