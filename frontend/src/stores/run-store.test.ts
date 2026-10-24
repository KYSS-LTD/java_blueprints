import { beforeEach, describe, expect, it, vi } from "vitest";

import type { RunEvent } from "@/lib/api/sse";

let emit: (event: RunEvent) => void = () => {};
const stop = vi.fn();
const open = vi.fn((_runId: number, onEvent: (event: RunEvent) => void) => {
  emit = onEvent;
  return stop;
});

vi.mock("@/lib/api/sse", () => ({ openRunEvents: open }));

const { useRunStore } = await import("./run-store");

beforeEach(() => {
  useRunStore.getState().clear();
  open.mockClear();
  stop.mockClear();
});

describe("run-store", () => {
  it("открывает один поток на запуск и складывает события в состояние", () => {
    useRunStore.getState().track(5, { label: "Запуск Main" });
    useRunStore.getState().track(5);
    expect(open).toHaveBeenCalledTimes(1);

    emit({ event: "status", data: { status: "running" } });
    emit({ event: "log", data: { stream: "stdout", text: "ok\n" } });
    const entry = useRunStore.getState().entries[5];
    expect(entry.label).toBe("Запуск Main");
    expect(entry.state.status).toBe("running");
    expect(entry.state.lines[0].text).toBe("ok\n");
    expect(useRunStore.getState().order).toEqual([5]);
  });

  it("clear закрывает потоки и очищает журнал", () => {
    useRunStore.getState().track(7);
    useRunStore.getState().clear();
    expect(stop).toHaveBeenCalledTimes(1);
    expect(useRunStore.getState().order).toEqual([]);
  });
});
