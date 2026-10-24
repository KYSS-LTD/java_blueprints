import type { RunEvent } from "@/lib/api/sse";
import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];

export type RunStatus = Schemas["StatusData"]["status"];
export type LogStream = Schemas["LogData"]["stream"];
export type Diagnostic = Schemas["Diagnostic"];
export type RunSummary = Schemas["RunSummary"];

export type ConsoleLine = { stream: LogStream; text: string };

/** Состояние запуска, свёрнутое из потока SSE-событий (docs/08-api.md § 9). */
export type RunState = {
  status: RunStatus | "idle";
  lines: ConsoleLine[];
  items: Schemas["ItemData"][];
  gate: Schemas["GateData"][];
  steps: Schemas["StepData"][];
  progress: Schemas["ProgressData"] | null;
  result: RunSummary | null;
  error: Schemas["ErrorData"] | null;
  /** Диагностики javac из результата сборки: по ним расставляются маркеры в редакторе. */
  diagnostics: Diagnostic[];
  done: boolean;
};

export const initialRunState: RunState = {
  status: "idle",
  lines: [],
  items: [],
  gate: [],
  steps: [],
  progress: null,
  result: null,
  error: null,
  diagnostics: [],
  done: false,
};

export function isRunning(state: RunState): boolean {
  return state.status === "queued" || state.status === "running";
}

function diagnosticsOf(summary: RunSummary): Diagnostic[] {
  const found = summary.data?.diagnostics;
  return Array.isArray(found) ? (found as Diagnostic[]) : [];
}

export function reduceRunEvent(state: RunState, event: RunEvent): RunState {
  switch (event.event) {
    case "status":
      return { ...state, status: event.data.status };
    case "log": {
      // Подряд идущие куски одного потока склеиваем: stdout приходит порциями без границ строк.
      const last = state.lines[state.lines.length - 1];
      if (last && last.stream === event.data.stream && (last.stream === "stdout" || last.stream === "stderr")) {
        const lines = state.lines.slice(0, -1);
        lines.push({ stream: last.stream, text: last.text + event.data.text });
        return { ...state, lines };
      }
      return { ...state, lines: [...state.lines, { stream: event.data.stream, text: event.data.text }] };
    }
    case "item":
      return { ...state, items: [...state.items, event.data] };
    case "gate":
      return { ...state, gate: [...state.gate, event.data] };
    case "step":
      return { ...state, steps: [...state.steps, event.data] };
    case "progress":
      return { ...state, progress: event.data };
    case "result":
      return { ...state, result: event.data.summary, diagnostics: diagnosticsOf(event.data.summary) };
    case "error":
      return { ...state, error: event.data };
    case "done":
      return { ...state, done: true };
  }
}

export function reduceRunEvents(events: RunEvent[], from: RunState = initialRunState): RunState {
  return events.reduce(reduceRunEvent, from);
}
