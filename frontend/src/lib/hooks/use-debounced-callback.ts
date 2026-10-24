"use client";

import { useCallback, useEffect, useRef } from "react";

/** Функция с задержкой: вызывается с последними аргументами после паузы `delayMs`. */
export function useDebouncedCallback<Args extends unknown[]>(
  callback: (...args: Args) => void,
  delayMs: number,
): (...args: Args) => void {
  const latest = useRef(callback);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    latest.current = callback;
  }, [callback]);
  useEffect(() => () => clearTimeout(timer.current), []);

  return useCallback(
    (...args: Args) => {
      clearTimeout(timer.current);
      timer.current = setTimeout(() => latest.current(...args), delayMs);
    },
    [delayMs],
  );
}
