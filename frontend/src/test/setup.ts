import "@testing-library/jest-dom/vitest";

import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom не реализует ResizeObserver, а Radix (ScrollArea и др.) его использует.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (!("ResizeObserver" in globalThis)) {
  Object.defineProperty(globalThis, "ResizeObserver", { value: ResizeObserverStub });
}

// React Flow читает матрицу преобразования через DOMMatrixReadOnly, которой в jsdom нет.
class DOMMatrixReadOnlyStub {
  m22 = 1;
  constructor(transform?: string) {
    const scale = transform?.match(/scale\(([\d.]+)\)/)?.[1];
    if (scale !== undefined) this.m22 = Number(scale);
  }
}
if (!("DOMMatrixReadOnly" in globalThis)) {
  Object.defineProperty(globalThis, "DOMMatrixReadOnly", { value: DOMMatrixReadOnlyStub });
}

// matchMedia нужен библиотекам интерфейса (например, sonner); в тестах экран «узкий».
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    value: (query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {}, // next-themes слушает по старому интерфейсу
      removeListener: () => {},
    }),
  });
}

// Экраны грузят данные и раскладывают граф асинхронно: на загруженной машине секунды по умолчанию мало.
configure({ asyncUtilTimeout: 5000 });

afterEach(() => cleanup());
