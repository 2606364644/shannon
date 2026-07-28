import { Sun, Moon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/lib/theme-context";
import { resolveEffectiveTheme } from "@/lib/theme";

export function ThemeToggle() {
  const { t } = useTranslation();
  const { theme, setTheme } = useTheme();
  const effective = resolveEffectiveTheme(theme);

  function toggle() {
    // 快捷翻转：切到 effective 的反色并落为显式态。
    // system 用户点一下 → 退出 system，落到显式 dark/light（"我要明确换到对面"的语义）。
    setTheme(effective === "dark" ? "light" : "dark");
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggle}
      aria-label={t("theme.toggleAria")}
      title={effective === "dark" ? t("theme.toLight") : t("theme.toDark")}
    >
      {effective === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </Button>
  );
}
