"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import { refreshProject, startGeneration } from "@/lib/api/actions";
import { flushDrafts } from "@/lib/edit/session";
import { ru } from "@/lib/i18n/ru";
import { isRunning } from "@/lib/run/run-state";
import { useRunEvents } from "@/lib/run/use-run-events";
import { useRunStore } from "@/stores/run-store";

const t = ru.generateDialog;

function percent(value: unknown): string {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "—";
}

type Props = { projectId: number; open: boolean; onOpenChange: (open: boolean) => void };

/** Генерация сценариев: поиск по покрытию блоков, прогресс и итог «покрытие до → после». */
export function GenerateDialog({ projectId, open, onOpenChange }: Props) {
  const [busy, setBusy] = useState(false);
  return (
    <Dialog open={open} onOpenChange={(next) => !busy && onOpenChange(next)}>
      <DialogContent className="sm:max-w-lg">
        {/* тело монтируется при каждом открытии: прежний результат не показывается */}
        <GenerateBody projectId={projectId} onBusy={setBusy} onClose={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function GenerateBody({
  projectId,
  onBusy,
  onClose,
}: {
  projectId: number;
  onBusy: (busy: boolean) => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [runId, setRunId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);
  const state = useRunEvents(runId);
  const running = starting || (runId !== null && isRunning(state));
  const data = state.result?.data ?? null;

  useEffect(() => {
    onBusy(running);
  }, [running, onBusy]);

  useEffect(() => {
    if (state.done) void refreshProject(queryClient, projectId);
  }, [state.done, queryClient, projectId]);

  const start = async () => {
    setStarting(true);
    try {
      await flushDrafts(queryClient, projectId);
      const id = await startGeneration(projectId);
      useRunStore.getState().track(id, { label: ru.tests.generate, kind: "generate" });
      setRunId(id);
    } catch (error) {
      toast.error(t.failed, { description: error instanceof Error ? error.message : undefined });
    } finally {
      setStarting(false);
    }
  };

  const warnings = Array.isArray(data?.warnings) ? (data.warnings as string[]) : [];
  const stop = typeof data?.stop_reason === "string" ? data.stop_reason : null;

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t.title}</DialogTitle>
        <DialogDescription>{t.description}</DialogDescription>
      </DialogHeader>

      {runId !== null && (
        <div className="space-y-3 text-sm" aria-live="polite">
          {running && (
            <>
              <p className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="size-4 animate-spin" aria-hidden /> {t.running}
              </p>
              <Progress
                value={
                  state.progress
                    ? (state.progress.done / Math.max(state.progress.total, 1)) * 100
                    : undefined
                }
              />
            </>
          )}
          {state.error && <p className="text-destructive">{state.error.message}</p>}
          {state.done && state.result && (
            <div className="space-y-2" data-testid="generate-result">
              <p className="font-medium">{state.result.message}</p>
              <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-xs">
                <dt className="text-muted-foreground">{t.coverage}</dt>
                <dd className="text-right font-mono">
                  {percent(data?.coverage_before)} → {percent(data?.coverage_after)}
                </dd>
                <dt className="text-muted-foreground">{t.created}</dt>
                <dd className="text-right font-mono">{state.result.total ?? 0}</dd>
                <dt className="text-muted-foreground">{t.runs}</dt>
                <dd className="text-right font-mono">{String(data?.runs ?? "—")}</dd>
                <dt className="text-muted-foreground">{t.seconds}</dt>
                <dd className="text-right font-mono">{String(data?.elapsed_s ?? "—")}</dd>
              </dl>
              {stop && (
                <p className="text-xs text-muted-foreground">
                  {t.stop[stop as keyof typeof t.stop] ?? stop}
                </p>
              )}
              {state.items.length > 0 && (
                <ul className="max-h-32 space-y-0.5 overflow-y-auto rounded border p-2 font-mono text-[11px]">
                  {state.items.map((item) => (
                    <li key={item.scenario_id ?? item.name}>{item.name}</li>
                  ))}
                </ul>
              )}
              {warnings.length > 0 && (
                <ul className="space-y-0.5 text-xs text-severity-medium" aria-label={t.warnings}>
                  {warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}

      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={running}>
          {state.done ? t.close : ru.common.cancel}
        </Button>
        {!state.done && (
          <Button onClick={() => void start()} disabled={running}>
            {running ? <Loader2 className="animate-spin" /> : <Sparkles />}
            {running ? t.starting : t.start}
          </Button>
        )}
      </DialogFooter>
    </>
  );
}
