import { create } from "zustand";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/queries";
import type { components } from "@/lib/api/schema";
import { openRunEvents } from "@/lib/api/sse";
import { useDebugStore } from "@/stores/debug-store";
import { initialRunState, reduceRunEvent, type RunState } from "@/lib/run/run-state";

export type RunRequest = components["schemas"]["RunRequest"];

export type RunEntry = {
  id: number;
  label: string;
  kind: RunRequest["kind"];
  stdin: string | null;
  args: string[];
  startedAt: number;
  state: RunState;
};

type RunStore = {
  /** Запуски в порядке создания (новые в конце). */
  order: number[];
  entries: Record<number, RunEntry>;
  /** Подписаться на события запуска (повторный вызов ничего не делает). */
  track: (runId: number, meta?: Partial<Pick<RunEntry, "label" | "kind" | "stdin" | "args">>) => void;
  /** Создать запуск и сразу подписаться на его события. */
  start: (projectId: number, request: RunRequest, label?: string) => Promise<number>;
  clear: () => void;
};

const unsubscribers = new Map<number, () => void>();

export const useRunStore = create<RunStore>((set, get) => ({
  order: [],
  entries: {},

  track(runId, meta = {}) {
    if (get().entries[runId]) return;
    const entry: RunEntry = {
      id: runId,
      label: meta.label ?? `Запуск #${runId}`,
      kind: meta.kind ?? "run",
      stdin: meta.stdin ?? null,
      args: meta.args ?? [],
      startedAt: Date.now(),
      state: initialRunState,
    };
    set((s) => ({ order: [...s.order, runId], entries: { ...s.entries, [runId]: entry } }));
    const stop = openRunEvents(runId, (event) => {
      set((s) => {
        const current = s.entries[runId];
        if (!current) return s;
        return {
          entries: { ...s.entries, [runId]: { ...current, state: reduceRunEvent(current.state, event) } },
        };
      });
      if (event.event === "done") {
        unsubscribers.delete(runId);
        // счётчики трассировки лежат в итоге запуска, а не в событиях SSE
        const finished = get().entries[runId];
        if (finished?.kind === "trace" && finished.state.status === "done") {
          void useDebugStore.getState().load(runId);
        }
      }
    });
    unsubscribers.set(runId, stop);
  },

  async start(projectId, request, label) {
    const ref = unwrap(
      await api.POST("/api/projects/{project_id}/runs", {
        params: { path: { project_id: projectId } },
        body: request,
      }),
    );
    get().track(ref.run_id, {
      label,
      kind: request.kind,
      stdin: request.stdin ?? null,
      args: request.args ?? [],
    });
    return ref.run_id;
  },

  clear() {
    for (const stop of unsubscribers.values()) stop();
    unsubscribers.clear();
    set({ order: [], entries: {} });
  },
}));
