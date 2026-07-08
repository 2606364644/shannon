import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";

export function LanguageSwitcher() {
  const { t, i18n } = useTranslation();
  const isZh = (i18n.language ?? "zh").startsWith("zh");
  return (
    <Button
      variant="ghost"
      size="sm"
      className="px-2 text-xs"
      aria-label={t("common.langSwitchAria")}
      onClick={() => i18n.changeLanguage(isZh ? "en" : "zh")}
    >
      {isZh ? "EN" : "中"}
    </Button>
  );
}

export default LanguageSwitcher;
