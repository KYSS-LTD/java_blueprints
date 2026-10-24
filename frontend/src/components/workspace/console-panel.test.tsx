import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { ru } from "@/lib/i18n/ru";
import { initialRunState, reduceRunEvents } from "@/lib/run/run-state";
import { type RunEntry, useRunStore } from "@/stores/run-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

import { ConsolePanel } from "./console-panel";

function entry(id: number, state: RunEntry["state"], extra: Partial<RunEntry> = {}): RunEntry {
  return { id, label: "Запуск #" + id, kind: "run", stdin: null, args: [], startedAt: 0, state, ...extra };
}

function seed(...entries: RunEntry[]) {
  useRunStore.setState({
    order: entries.map((e) => e.id),
    entries: Object.fromEntries(entries.map((e) => [e.id, e])),
  });
}

beforeEach(() => {
  useRunStore.setState({ order: [], entries: {} });
  useWorkspaceStore.getState().reset();
});

describe("ConsolePanel", () => {
  it("без запусков показывает подсказку", () => {
    render(<ConsolePanel />);
    expect(screen.getByText(ru.console.empty)).toBeInTheDocument();
  });

  it("разделяет stdout и stderr, показывает код выхода и время", () => {
    const state = reduceRunEvents([
      { event: "status", data: { status: "running" } },
      { event: "log", data: { stream: "stdout", text: "Привет, мир\n" } },
      { event: "log", data: { stream: "stderr", text: "предупреждение\n" } },
      {
        event: "result",
        data: {
          summary: {
            passed: 1,
            total: 1,
            coverage: null,
            version_status: null,
            message: null,
            data: { exit_code: 3, duration_ms: 187 },
          },
        },
      },
      { event: "status", data: { status: "done" } },
      { event: "done", data: {} },
    ]);
    seed(entry(5, state));
    render(<ConsolePanel />);
    const output = screen.getByTestId("console-output");
    expect(within(output).getByText("Привет, мир")).toHaveAttribute("data-stream", "stdout");
    expect(within(output).getByText("предупреждение")).toHaveAttribute("data-stream", "stderr");
    expect(screen.getByText(/код выхода/)).toHaveTextContent("3");
    expect(screen.getByText(/187 мс/)).toBeInTheDocument();
  });

  it("у выполняющегося запуска есть кнопка остановки", () => {
    seed(entry(6, reduceRunEvents([{ event: "status", data: { status: "running" } }])));
    render(<ConsolePanel />);
    expect(screen.getByRole("button", { name: new RegExp(ru.console.cancel) })).toBeInTheDocument();
    expect(screen.getByText(ru.console.running)).toBeInTheDocument();
  });

  it("диагностики компиляции кликабельны и выбирают файл в коде", async () => {
    const user = userEvent.setup();
    const diagnostic = {
      file: "Main.java",
      line: 12,
      column: 5,
      end_line: null,
      end_column: null,
      severity: "error" as const,
      message: "cannot find symbol",
      node_id: null,
    };
    seed(entry(7, { ...initialRunState, status: "failed", done: true, diagnostics: [diagnostic] }));
    render(<ConsolePanel />);
    await user.click(screen.getByRole("button", { name: /cannot find symbol/ }));
    expect(useWorkspaceStore.getState().activeFile).toBe("Main.java");
    expect(useWorkspaceStore.getState().selection?.range?.start_line).toBe(12);
  });

  it("«Очистить» убирает журнал", async () => {
    const user = userEvent.setup();
    seed(entry(8, initialRunState));
    render(<ConsolePanel />);
    await user.click(screen.getByRole("button", { name: ru.console.clear }));
    expect(useRunStore.getState().order).toEqual([]);
  });
});
