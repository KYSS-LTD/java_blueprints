"use client";

import { useEffect } from "react";

import { initialRunState, type RunState } from "@/lib/run/run-state";
import { useRunStore } from "@/stores/run-store";

/**
 * Состояние запуска по SSE (docs/09 § 5). Подписка живёт в `run-store`, поэтому состояние
 * не теряется при смене вкладок, а повторные вызовы с тем же `runId` не открывают второй поток.
 */
export function useRunEvents(runId: number | null): RunState {
  const state = useRunStore((s) => (runId === null ? undefined : s.entries[runId]?.state));
  useEffect(() => {
    if (runId !== null) useRunStore.getState().track(runId);
  }, [runId]);
  return state ?? initialRunState;
}
