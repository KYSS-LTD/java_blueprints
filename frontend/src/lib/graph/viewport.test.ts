import { describe, expect, it } from "vitest";

import { READABLE_ZOOM, sameViewport, startViewport, TOP_MARGIN, topBlock } from "./viewport";

describe("startViewport", () => {
  const size = { width: 800, height: 400 };

  it("ставит верхний блок вверху по центру окна в читаемом масштабе", () => {
    const top = { x: 300, y: 0, width: 200, height: 60 };
    const view = startViewport(top, size);
    expect(view.zoom).toBe(READABLE_ZOOM);
    // центр блока (400, 30) на экране попадает в середину окна по ширине
    expect((top.x + top.width / 2) * view.zoom + view.x).toBeCloseTo(400);
    // и верхний край блока — у верхнего края окна с небольшим отступом
    expect(top.y * view.zoom + view.y).toBeGreaterThan(0);
    expect(top.y * view.zoom + view.y).toBeLessThan(size.height / 4 + 1);
  });

  it("учитывает смещение схемы по вертикали и свой масштаб", () => {
    const view = startViewport({ x: 0, y: 100, width: 100, height: 40 }, size, 1);
    expect(view).toEqual({ x: 350, y: TOP_MARGIN - 100, zoom: 1 });
  });

  it("в низком окне отступ сверху не больше четверти высоты", () => {
    const view = startViewport({ x: 0, y: 0, width: 100, height: 40 }, { width: 800, height: 60 });
    expect(view.y).toBe(15);
  });
});

describe("topBlock", () => {
  it("берёт самый верхний блок, при равенстве — левый", () => {
    const boxes = [
      { x: 50, y: 80, width: 1, height: 1 },
      { x: 90, y: 10, width: 1, height: 1 },
      { x: 20, y: 10, width: 1, height: 1 },
    ];
    expect(topBlock(boxes)).toBe(boxes[2]);
  });

  it("для пустого списка ничего не возвращает", () => {
    expect(topBlock([])).toBeUndefined();
  });
});

describe("sameViewport", () => {
  it("сравнивает с точностью до пикселя и тысячной масштаба", () => {
    const base = { x: 10, y: 20, zoom: 0.7 };
    expect(sameViewport(base, { x: 10.4, y: 19.6, zoom: 0.7004 })).toBe(true);
    expect(sameViewport(base, { x: 12, y: 20, zoom: 0.7 })).toBe(false);
    expect(sameViewport(base, { x: 10, y: 20, zoom: 0.8 })).toBe(false);
  });
});
