import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { RunEvent } from "@/lib/api/sse";

let emit: (event: RunEvent) => void = () => {};
const open = vi.fn((_runId: number, onEvent: (event: RunEvent) => void) => {
  emit = onEvent;
  return () => {};
});
vi.mock("@/lib/api/sse", () => ({ openRunEvents: open }));

const { useRunEvents } = await import("./use-run-events");
const { useRunStore } = await import("@/stores/run-store");

beforeEach(() => {
  useRunStore.getState().clear();
  open.mockClear();
});

describe("useRunEvents", () => {
  it("без запуска возвращает пустое состояние и ничего не открывает", () => {
    const { result } = renderHook(() => useRunEvents(null));
    expect(result.current.status).toBe("idle");
    expect(open).not.toHaveBeenCalled();
  });

  it("подписывается на запуск и обновляет состояние по событиям", () => {
    const { result } = renderHook(() => useRunEvents(11));
    expect(open).toHaveBeenCalledWith(11, expect.any(Function));
    act(() => emit({ event: "status", data: { status: "running" } }));
    act(() => emit({ event: "log", data: { stream: "stdout", text: "готово\n" } }));
    expect(result.current.status).toBe("running");
    expect(result.current.lines).toEqual([{ stream: "stdout", text: "готово\n" }]);
  });

  it("два потребителя одного запуска делят один поток", () => {
    renderHook(() => useRunEvents(12));
    renderHook(() => useRunEvents(12));
    expect(open).toHaveBeenCalledTimes(1);
  });
});
