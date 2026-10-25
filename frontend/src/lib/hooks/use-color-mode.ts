"use client";

import { useTheme } from "next-themes";

import { useIsClient } from "./use-is-client";

export type ColorMode = "light" | "dark";

/** Тема интерфейса для компонентов, которые красятся не через CSS (React Flow, Monaco); до гидратации — тёмная. */
export function useColorMode(): ColorMode {
  const { resolvedTheme } = useTheme();
  const client = useIsClient();
  return client && resolvedTheme === "light" ? "light" : "dark";
}
