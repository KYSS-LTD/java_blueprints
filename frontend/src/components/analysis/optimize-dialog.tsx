"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Check, Loader2, SkipForward, Sparkles, Undo2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { MeasuredChart, SpeedupLine } from "@/components/analysis/performance-block";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { applyOptimize, type OptimizeStep, planOptimize, refreshProject } from "@/lib/api/actions";
import { projectQuery } from "@/lib/api/queries";
import { placeOfFindingId, readOptimize } from "@/lib/analysis/view";
import { flushDrafts, moveHead } from "@/lib/edit/session";
import { useEditLock } from "@/lib/edit/use-edit-lock";
import { ru } from "@/lib/i18n/ru";
import { isInProgress } from "@/lib/run/run-state";
import { useRunEvents } from "@/lib/run/use-run-events";
import { cn } from "@/lib/utils";
import { useRunStore } from "@/stores/run-store";

const t = ru.optimizeDialog;

type Props = { projectId: number; open: boolean; onOpenChange: (open: boolean) => void };

/** «Оптимизировать»: шаги с галочками → прогресс по шагам с проверкой → отчёт «до/после» и откат всей пачки. */
export function OptimizeDialog({ projectId, open, onOpenChange }: Props) {
  const [busy, setBusy] = useState(false);
  return (
    <Dialog open={open} onOpenChange={(next) => !busy && onOpenChange(next)}>
      <DialogContent
        className="max-h-[90vh] overflow-y-auto sm:max-w-2xl"
        data-testid="optimize-dialog"
      >
        {/* тело монтируется при каждом открытии: прежний план и отчёт не показываются */}
        <OptimizeBody projectId={projectId} onBusy={setBusy} onClose={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function StepMark({ status }: { status: "pending" | "applying" | "passed" | "skipped" }) {
  if (status === "applying")
    return <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-hidden />;
  if (status === "passed") return <Check className="size-3.5 text-node-return" aria-hidden />;
  if (status === "skipped")
    return <SkipForward className="size-3.5 text-severity-medium" aria-hidden />;
  return <span className="size-3.5" aria-hidden />;
}

/** Где применяется шаг: имя метода из id находки (названия у шагов одного правила одинаковы). */
function StepPlace({ step }: { step: OptimizeStep }) {
  const place = placeOfFindingId(step.finding_id);
  return place ? (
    <span>
      {t.in} <code>{place}</code>
    </span>
  ) : null;
}

function PlanStep({
  step,
  checked,
  onChange,
}: {
  step: OptimizeStep;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <li
      className="flex items-start gap-2 rounded-md border p-2 text-sm"
      data-testid="optimize-step"
    >
      <Checkbox
        checked={checked}
        onCheckedChange={(value) => onChange(value === true)}
        aria-label={step.title}
        className="mt-0.5"
      />
      <div className="min-w-0 flex-1">
        <p className="font-medium">{step.title}</p>
        <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <Badge variant="outline" className="font-mono text-[10px]">
            {step.fix_id}
          </Badge>
          <StepPlace step={step} />
          {step.effect.complexity_before && (
            <>
              <code>{step.effect.complexity_before}</code>
              {step.effect.complexity_after && (
                <>
                  <ArrowRight className="size-3" aria-hidden />
                  <code className="text-node-return">{step.effect.complexity_after}</code>
                </>
              )}
            </>
          )}
        </p>
      </div>
    </li>
  );
}

function OptimizeBody({
  projectId,
  onBusy,
  onClose,
}: {
  projectId: number;
  onBusy: (busy: boolean) => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const project = useQuery(projectQuery(projectId));
  const head = project.data?.head_version_id;
  const [started, setStarted] = useState<{
    runId: number;
    baseVersionId: number;
    ids: string[];
  } | null>(null);
  const [starting, setStarting] = useState(false);
  const [rollingBack, setRollingBack] = useState(false);
  const [rolledBack, setRolledBack] = useState(false);
  const [unchecked, setUnchecked] = useState<ReadonlySet<string>>(new Set());

  // план строится один раз: после запуска HEAD меняется, а план относится к версии до оптимизации
  const plan = useQuery({
    queryKey: ["fixes", projectId, "optimize-plan"],
    queryFn: () => planOptimize(projectId),
    enabled: head !== undefined && started === null,
    retry: false,
    gcTime: 0,
  });
  const steps = useMemo(() => plan.data?.steps ?? [], [plan.data]);
  const chosen = useMemo(() => steps.filter((s) => !unchecked.has(s.step_id)), [steps, unchecked]);

  const state = useRunEvents(started?.runId ?? null);
  const running = starting || rollingBack || (started !== null && isInProgress(state));
  useEditLock(running);
  useEffect(() => {
    onBusy(running);
  }, [running, onBusy]);

  useEffect(() => {
    if (state.done) void refreshProject(queryClient, projectId);
  }, [state.done, queryClient, projectId]);

  const report = state.done && state.status === "done" ? readOptimize(state.result) : null;
  const statusOf = useMemo(() => {
    const map = new Map<string, "applying" | "passed" | "skipped">();
    for (const step of state.steps) map.set(step.step_id, step.status);
    return map;
  }, [state.steps]);
  const reasonOf = useMemo(() => {
    const map = new Map<string, string>();
    for (const step of state.steps) if (step.reason) map.set(step.step_id, step.reason);
    return map;
  }, [state.steps]);
  const lastLog = state.lines[state.lines.length - 1]?.text;

  const start = async () => {
    if (head === undefined) return;
    setStarting(true);
    try {
      await flushDrafts(queryClient, projectId);
      const ids = chosen.map((s) => s.step_id);
      const runId = await applyOptimize(projectId, head, ids);
      useRunStore.getState().track(runId, { label: ru.header.optimize, kind: "optimize" });
      setStarted({ runId, baseVersionId: head, ids });
    } catch (error) {
      toast.error(t.startFailed, {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setStarting(false);
    }
  };

  const rollback = async () => {
    if (!started) return;
    setRollingBack(true);
    try {
      await moveHead(queryClient, projectId, { restore: started.baseVersionId });
      setRolledBack(true);
      toast.success(t.rolledBack);
      await refreshProject(queryClient, projectId);
    } catch (error) {
      toast.error(t.rollbackFailed, {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setRollingBack(false);
    }
  };

  const view: "plan" | "run" | "report" = started === null ? "plan" : state.done ? "report" : "run";
  const progress = state.progress;

  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          <Sparkles className="size-4" aria-hidden /> {t.title}
        </DialogTitle>
        <DialogDescription>{t.description}</DialogDescription>
      </DialogHeader>

      {view === "plan" && (
        <div className="space-y-3">
          {plan.isLoading && <Skeleton className="h-32" aria-label={t.loading} />}
          {plan.error && (
            <p className="text-sm text-destructive" role="alert">
              {t.failed}: {plan.error.message}
            </p>
          )}
          {plan.data && steps.length === 0 && (
            <div className="rounded-md border p-4 text-center text-sm" data-testid="optimize-empty">
              <p className="font-medium">{t.empty}</p>
              <p className="text-xs text-muted-foreground">{t.emptyHint}</p>
            </div>
          )}
          {plan.data && steps.length > 0 && (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                <label className="flex items-center gap-2">
                  <Checkbox
                    checked={
                      unchecked.size === 0 ? true : chosen.length === 0 ? false : "indeterminate"
                    }
                    onCheckedChange={(value) =>
                      setUnchecked(
                        value === true ? new Set() : new Set(steps.map((s) => s.step_id)),
                      )
                    }
                    aria-label={t.selectAll}
                  />
                  {t.selected} {chosen.length} {t.of} {steps.length}
                </label>
                {plan.data.effect.complexity_before && (
                  <span className="flex items-center gap-1.5" data-testid="optimize-effect">
                    {t.complexity}:
                    <code className="rounded bg-muted px-1.5 py-0.5">
                      {plan.data.effect.complexity_before}
                    </code>
                    {plan.data.effect.complexity_after && (
                      <>
                        <ArrowRight className="size-3 text-muted-foreground" aria-hidden />
                        <code className="rounded bg-node-return/15 px-1.5 py-0.5 text-node-return">
                          {plan.data.effect.complexity_after}
                        </code>
                      </>
                    )}
                  </span>
                )}
              </div>
              <ul className="space-y-1.5" aria-label={t.steps}>
                {steps.map((step) => (
                  <PlanStep
                    key={step.step_id}
                    step={step}
                    checked={!unchecked.has(step.step_id)}
                    onChange={(checked) =>
                      setUnchecked((current) => {
                        const next = new Set(current);
                        if (checked) next.delete(step.step_id);
                        else next.add(step.step_id);
                        return next;
                      })
                    }
                  />
                ))}
              </ul>
              <p className="text-xs text-muted-foreground">{t.stepsNote}</p>
            </>
          )}
        </div>
      )}

      {view !== "plan" && started && (
        <div className="space-y-3" aria-live="polite" data-testid="optimize-progress">
          {view === "run" && (
            <>
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" aria-hidden /> {t.running}
                {lastLog && <span className="truncate text-xs">· {lastLog}</span>}
              </p>
              <Progress
                value={progress ? (progress.done / Math.max(progress.total, 1)) * 100 : undefined}
                aria-label={t.running}
              />
            </>
          )}
          <ul className="space-y-1" aria-label={t.steps}>
            {steps
              .filter((s) => started.ids.includes(s.step_id))
              .map((step) => {
                const status = statusOf.get(step.step_id) ?? "pending";
                const reason = reasonOf.get(step.step_id);
                return (
                  <li
                    key={step.step_id}
                    className="flex items-start gap-2 text-sm"
                    data-testid="optimize-run-step"
                    data-status={status}
                  >
                    <StepMark status={status} />
                    <div className="min-w-0 flex-1">
                      <span className={cn(status === "skipped" && "text-muted-foreground")}>
                        {step.title}
                      </span>
                      <span className="ml-1 text-xs text-muted-foreground">
                        <StepPlace step={step} />
                      </span>
                      <span className="ml-2 text-xs text-muted-foreground">{t.status[status]}</span>
                      {reason && <p className="text-xs text-severity-medium">{reason}</p>}
                    </div>
                  </li>
                );
              })}
          </ul>
        </div>
      )}

      {view === "report" && started && (
        <section
          className="space-y-3 rounded-md border p-3 text-sm"
          data-testid="optimize-report"
          aria-label={t.report}
        >
          {state.error && <p className="text-destructive">{state.error.message}</p>}
          {state.result?.message && <p className="font-medium">{state.result.message}</p>}
          {report && (
            <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-xs">
              <dt className="text-muted-foreground">{t.applied}</dt>
              <dd className="text-right font-mono">
                {report.applied.length} {t.of} {started.ids.length}
              </dd>
              {report.complexityAfter && (
                <>
                  <dt className="text-muted-foreground">{t.complexity}</dt>
                  <dd className="text-right font-mono">
                    {plan.data?.effect.complexity_before ?? "—"} → {report.complexityAfter}
                  </dd>
                </>
              )}
              <dt className="text-muted-foreground">{t.behavior}</dt>
              <dd
                className={cn(
                  "text-right",
                  state.result?.version_status === "passed" && "text-node-return",
                  state.result?.version_status === "failed" && "text-severity-high",
                )}
              >
                {state.result?.version_status === "passed"
                  ? t.behaviorPassed
                  : state.result?.version_status === "failed"
                    ? t.behaviorFailed
                    : t.behaviorUnknown}
              </dd>
              <dt className="text-muted-foreground">{t.speedup}</dt>
              <dd className="text-right">
                <SpeedupLine measured={report.measured} />
              </dd>
            </dl>
          )}
          {report && Object.keys(report.skipped).length > 0 && (
            <div className="space-y-0.5 text-xs">
              <p className="font-semibold">{t.skipped}</p>
              <ul className="space-y-0.5 text-muted-foreground">
                {Object.entries(report.skipped).map(([id, reason]) => (
                  <li key={id}>
                    <code>{steps.find((s) => s.step_id === id)?.title ?? id}</code>
                    {placeOfFindingId(id) && ` (${t.in} ${placeOfFindingId(id)})`} — {reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {report?.measured && report.measured.samples.length > 1 && (
            <MeasuredChart measured={report.measured} />
          )}
          {rolledBack && <p className="text-xs text-muted-foreground">{t.rolledBack}</p>}
        </section>
      )}

      <DialogFooter>
        {view === "report" && !rolledBack && (report?.applied.length ?? 0) > 0 && (
          <Button
            variant="outline"
            onClick={() => void rollback()}
            disabled={rollingBack}
            data-testid="optimize-rollback"
          >
            {rollingBack ? <Loader2 className="animate-spin" /> : <Undo2 />}
            {rollingBack ? t.rollingBack : t.rollback}
          </Button>
        )}
        <Button variant="outline" onClick={onClose} disabled={running}>
          {view === "report" ? t.done : ru.common.cancel}
        </Button>
        {view === "plan" && (
          <Button
            onClick={() => void start()}
            disabled={starting || chosen.length === 0 || !plan.data}
            data-testid="optimize-start"
          >
            {starting ? <Loader2 className="animate-spin" /> : <Sparkles />}
            {starting ? t.starting : t.start}
          </Button>
        )}
      </DialogFooter>
    </>
  );
}
