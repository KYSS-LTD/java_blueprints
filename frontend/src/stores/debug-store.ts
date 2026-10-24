import { create } from "zustand";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/queries";
import { buildTrace, type DebugTrace } from "@/lib/debug/trace";
import { useWorkspaceStore } from "@/stores/workspace-store";

type DebugStore = {
  /** Последняя трассировка: на схемах показываются её счётчики. */
  trace: DebugTrace | null;
  /** Загрузить итог завершённого запуска `trace` и показать его на схемах. */
  load: (runId: number) => Promise<DebugTrace | null>;
  clear: () => void;
};

export const useDebugStore = create<DebugStore>((set) => ({
  trace: null,

  async load(runId) {
    const info = unwrap(await api.GET("/api/runs/{run_id}", { params: { path: { run_id: runId } } }));
    const trace = buildTrace(info);
    set({ trace });
    // оверлей включается сам: ради него трассировку и запускали
    if (trace) useWorkspaceStore.getState().setOverlay("coverage", true);
    return trace;
  },

  clear() {
    set({ trace: null });
    useWorkspaceStore.getState().setOverlay("coverage", false);
  },
}));
