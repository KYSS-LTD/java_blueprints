import type { Viewport } from "@xyflow/react";

/** Масштаб, в котором показывается начало крупной схемы: подписи блоков (10–11 px) читаются (в макете — 100%). */
export const READABLE_ZOOM = 0.85;

/** Масштаб вписанной схемы, ниже которого она превращается в картинку без подписей: тогда берётся `READABLE_ZOOM`. */
export const MIN_FIT_ZOOM = 0.5;

/** Отступ верхнего блока от верхнего края окна (ниже панели оверлеев), пиксели экрана. */
export const TOP_MARGIN = 60;

export type Box = { x: number; y: number; width: number; height: number };

/**
 * Камера для схемы, которая целиком не помещается в читаемом масштабе: читаемый масштаб, верхний блок
 * (начало метода) вверху по центру окна. Остальное достигается прокруткой, «Вписать в окно» показывает всё.
 */
export function startViewport(top: Box, size: { width: number; height: number }, zoom = READABLE_ZOOM): Viewport {
  return {
    x: size.width / 2 - (top.x + top.width / 2) * zoom,
    y: Math.min(TOP_MARGIN, size.height / 4) - top.y * zoom,
    zoom,
  };
}

/** Самый верхний из блоков верхнего уровня; `undefined`, если блоков нет. */
export function topBlock<T extends Box>(boxes: readonly T[]): T | undefined {
  let best: T | undefined;
  for (const box of boxes) if (!best || box.y < best.y || (box.y === best.y && box.x < best.x)) best = box;
  return best;
}

/** Та же камера с точностью до пикселя и тысячной масштаба. */
export function sameViewport(a: Viewport, b: Viewport): boolean {
  return Math.abs(a.x - b.x) < 1 && Math.abs(a.y - b.y) < 1 && Math.abs(a.zoom - b.zoom) < 0.001;
}
