"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";
import { useColorMode } from "@/lib/hooks/use-color-mode";
import { ru } from "@/lib/i18n/ru";

/** Переключатель светлой и тёмной темы (в макете — луна в правом углу шапки); выбор запоминается в браузере. */
export function ThemeToggle({ className }: { className?: string }) {
  const { setTheme } = useTheme();
  const mode = useColorMode();
  const label = mode === "dark" ? ru.theme.toLight : ru.theme.toDark;

  return (
    <Button
      variant="ghost"
      size="icon-sm"
      className={className}
      aria-label={label}
      title={label}
      data-testid="theme-toggle"
      onClick={() => setTheme(mode === "dark" ? "light" : "dark")}
    >
      {mode === "dark" ? <Sun /> : <Moon />}
    </Button>
  );
}
