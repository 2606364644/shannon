import { Sun, Moon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/lib/theme-context";
import { resolveEffectiveTheme, oppositeBaseTheme } from "@/lib/theme";

export function ThemeToggle() {
  const { t } = useTranslation();
  const { theme, setTheme } = useTheme();
  const effective = resolveEffectiveTheme(theme);

  function toggle() {
    // 快捷翻转：切到对侧 mode 的默认主题（dark→openai / light→graphite）并落为显式态。
    // system 用户点一下 → 退出 system；palette 主题一律翻到对侧默认
    // ——长尾主题选择走 SettingsPage 的主题选择器。
    setTheme(oppositeBaseTheme(effective));
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
