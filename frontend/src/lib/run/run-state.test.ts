import { describe, expect, it } from "vitest";

import type { RunEvent } from "@/lib/api/sse";
import eventsJson from "@/mocks/fixtures/run-8-events.json";

import { initialRunState, isRunning, reduceRunEvent, reduceRunEvents } from "./run-state";

const events = eventsJson as unknown as RunEvent[];

describe("reduceRunEvent", () => {
  it("сворачивает полный поток gate-запуска из фикстуры", () => {
    const state = reduceRunEvents(events);
    expect(state.status).toBe("done");
    expect(state.done).toBe(true);
    expect(state.items).toHaveLength(11);
    expect(state.items.filter((i) => i.verdict === "FAIL")).toHaveLength(1);
    expect(state.progress).toEqual({ done: 11, total: 11 });
    expect(state.gate.map((g) => g.stage)).toContain("compile");
    expect(state.result?.passed).toBe(10);
    expect(state.result?.coverage).toBeCloseTo(0.87);
  });

  it("не мутирует прошлое состояние", () => {
    const before = reduceRunEvent(initialRunState, { event: "status", data: { status: "queued" } });
    const after = reduceRunEvent(before, { event: "log", data: { stream: "info", text: "Сборка…" } });
    expect(before.lines).toHaveLength(0);
    expect(after.lines).toHaveLength(1);
    expect(isRunning(before)).toBe(true);
    expect(isRunning(reduceRunEvent(before, { event: "status", data: { status: "done" } }))).toBe(false);
  });

  it("склеивает подряд идущие куски stdout, но не служебные строки", () => {
    const state = reduceRunEvents([
      { event: "log", data: { stream: "info", text: "Запуск Main" } },
      { event: "log", data: { stream: "stdout", text: "Привет, " } },
      { event: "log", data: { stream: "stdout", text: "мир\n" } },
      { event: "log", data: { stream: "stderr", text: "ошибка\n" } },
      { event: "log", data: { stream: "info", text: "ещё" } },
      { event: "log", data: { stream: "info", text: "ещё" } },
    ]);
    expect(state.lines).toEqual([
      { stream: "info", text: "Запуск Main" },
      { stream: "stdout", text: "Привет, мир\n" },
      { stream: "stderr", text: "ошибка\n" },
      { stream: "info", text: "ещё" },
      { stream: "info", text: "ещё" },
    ]);
  });

  it("достаёт диагностики сборки из результата и запоминает ошибку", () => {
    const diagnostic = {
      file: "Main.java",
      line: 3,
      column: 5,
      end_line: null,
      end_column: null,
      severity: "error",
      message: "cannot find symbol",
      node_id: null,
    };
    const state = reduceRunEvents([
      { event: "error", data: { code: "COMPILE_ERROR", message: "Ошибки компиляции: 1" } },
      {
        event: "result",
        data: {
          summary: {
            passed: null,
            total: null,
            coverage: null,
            version_status: null,
            message: "Ошибки компиляции: 1",
            data: { stage: "compile", diagnostics: [diagnostic] },
          },
        },
      },
    ]);
    expect(state.error?.code).toBe("COMPILE_ERROR");
    expect(state.diagnostics).toEqual([diagnostic]);
  });
});
