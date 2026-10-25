"use client";

import { type RefObject, useLayoutEffect, useState } from "react";

/** Ширина элемента в пикселях: `null` до первого измерения (оно идёт до отрисовки), дальше — по `ResizeObserver`. */
export function useElementWidth(ref: RefObject<HTMLElement | null>): number | null {
  const [width, setWidth] = useState<number | null>(null);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    setWidth(element.getBoundingClientRect().width);
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);

  return width;
}
