"use client";

import { useSyncExternalStore } from "react";

/** Совпадает ли медиазапрос; на сервере и при первой отрисовке — `fallback`. */
export function useMediaQuery(query: string, fallback = false): boolean {
  return useSyncExternalStore(
    (notify) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", notify);
      return () => list.removeEventListener("change", notify);
    },
    () => window.matchMedia(query).matches,
    () => fallback,
  );
}
