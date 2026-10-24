"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { GateStages } from "@/components/workspace/gate-stages";
import {
  type ApplyResult,
  acceptCandidate,
  refreshProject,
  rejectCandidate,
} from "@/lib/api/actions";
import { projectQuery } from "@/lib/api/queries";
import { candidateState } from "@/lib/analysis/view";
import { flushDrafts } from "@/lib/edit/session";
import { useEditLock } from "@/lib/edit/use-edit-lock";
import { ru } from "@/lib/i18n/ru";
import { isInProgress } from "@/lib/run/run-state";
import { diffHeadline } from "@/lib/run/tests-view";
import { useRunEvents } from "@/lib/run/use-run-events";
import { cn } from "@/lib/utils";
import { useRunStore } from "@/stores/run-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

const common = ru.candidate;

/** Тексты конкретного преобразования: что сказать об итоге проверки версии-кандидата. */
export type CandidateTexts = {
  /** Название запуска в списке запусков: «Исправление · OPT-02». */
  runLabel: string;
  applyFailed: string;
  outcome: {
    promoted: string;
    rejected: string;
    candidate: string;
    stale: string;
    unknown: string;
  };
  decided: { accepted: string; rejected: string };
};

export type CandidateRun = ReturnType<typeof useCandidateRun>;

/**
 * Применение преобразования как версии-кандидата: запуск, стадии проверки поведения, решение пользователя
 * («принять» и «отклонить» при красном gate) и запись нового эталона. Общее для исправлений,
 * выделения метода и декомпозиции.
 */
export function useCandidateRun(
  projectId: number,
  texts: CandidateTexts,
  options: { onBusy?: (busy: boolean) => void; onClose: () => void },
) {
  const { onBusy, onClose } = options;
  const queryClient = useQueryClient();
  const project = useQuery(projectQuery(projectId));
  const head = project.data?.head_version_id;
  const [applied, setApplied] = useState<{ candidateId: number; runId: number } | null>(null);
  const [starting, setStarting] = useState(false);
  const [decision, setDecision] = useState<"accepted" | "rejected" | null>(null);
  const [deciding, setDeciding] = useState(false);
  const state = useRunEvents(applied?.runId ?? null);
  const running = starting || (applied !== null && isInProgress(state));
  const candidate = state.done ? candidateState(state.result) : null;

  // пока сервер меняет HEAD, код в редакторе доступен только для чтения
  useEditLock(running);
  useEffect(() => {
    onBusy?.(running);
  }, [running, onBusy]);

  useEffect(() => {
    if (!state.done) return;
    void refreshProject(queryClient, projectId);
    if (candidate === "promoted") toast.success(texts.outcome.promoted);
  }, [state.done, candidate, queryClient, projectId, texts.outcome.promoted]);

  /** `begin` получает номер текущей версии и создаёт кандидата на сервере. */
  const apply = async (begin: (baseVersionId: number) => Promise<ApplyResult>) => {
    if (head === undefined) return;
    setStarting(true);
    try {
      await flushDrafts(queryClient, projectId);
      const result = await begin(head);
      useRunStore.getState().track(result.run_id, { label: texts.runLabel, kind: "gate" });
      setApplied({ candidateId: result.candidate_version_id, runId: result.run_id });
    } catch (error) {
      toast.error(texts.applyFailed, {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setStarting(false);
    }
  };

  const decide = async (kind: "accept" | "reject") => {
    if (!applied) return;
    setDeciding(true);
    try {
      if (kind === "accept") await acceptCandidate(projectId, applied.candidateId);
      else await rejectCandidate(projectId, applied.candidateId);
      setDecision(kind === "accept" ? "accepted" : "rejected");
      toast(kind === "accept" ? common.accepted : common.rejectedToast);
      await refreshProject(queryClient, projectId);
    } catch (error) {
      toast.error(common.decisionFailed, {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setDeciding(false);
    }
  };

  const rebaseline = async () => {
    try {
      await useRunStore
        .getState()
        .start(projectId, { kind: "baseline" }, ru.tests.started.baseline);
      useWorkspaceStore.getState().setBottomTab("tests");
      toast(common.rebaselineStarted);
      onClose();
    } catch (error) {
      toast.error(ru.tests.failedToStart, {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  };

  const finished = applied !== null && state.done;
  return {
    texts,
    head,
    applied,
    starting,
    running,
    state,
    candidate,
    decision,
    deciding,
    finished,
    // зелёный итог: версия стала текущей — сама после проверки или по решению пользователя
    good: decision === "accepted" || (decision === null && candidate === "promoted"),
    failed: state.items.filter((item) => item.verdict !== "PASS"),
    apply,
    decide,
    rebaseline,
  };
}

/** Ход и итог проверки поведения: стадии gate, прогресс, исход и сценарии, которые изменились. */
export function CandidateOutcome({ run, testId }: { run: CandidateRun; testId: string }) {
  const { texts, state, running, finished, good, candidate, decision, failed } = run;
  return (
    <div
      className="space-y-2 rounded-md border p-3 text-sm"
      aria-live="polite"
      data-testid={`${testId}-outcome`}
    >
      <GateStages gate={state.gate} />
      {running && (
        <>
          <p className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden /> {ru.tests.running}
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
      {finished && (
        <>
          <p
            className={cn(
              "flex items-start gap-2 font-medium",
              good ? "text-node-return" : "text-severity-medium",
            )}
            data-testid={`${testId}-outcome-message`}
            data-candidate={candidate ?? "none"}
            data-decision={decision ?? "none"}
          >
            {good ? (
              <Check className="mt-0.5 size-4 shrink-0" aria-hidden />
            ) : (
              <X className="mt-0.5 size-4 shrink-0" aria-hidden />
            )}
            {decision ? texts.decided[decision] : texts.outcome[candidate ?? "unknown"]}
          </p>
          {state.result?.message && (
            <p className="text-xs text-muted-foreground">{state.result.message}</p>
          )}
          {failed.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-semibold">{common.changedScenarios}</p>
              <ul className="space-y-0.5 text-xs">
                {failed.map((item) => (
                  <li key={item.scenario_id ?? item.name} className="flex gap-2">
                    <code>{item.name}</code>
                    <span className="text-muted-foreground">{diffHeadline(item)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {decision === "accepted" && (
            <p className="text-xs text-muted-foreground">{common.rebaselineHint}</p>
          )}
        </>
      )}
    </div>
  );
}

/** Кнопки решения по кандидату с красным gate и запись нового эталона после принятия. */
export function CandidateDecision({ run, testId }: { run: CandidateRun; testId: string }) {
  const { candidate, decision, deciding } = run;
  return (
    <>
      {candidate === "candidate" && decision === null && (
        <>
          <Button variant="outline" onClick={() => void run.decide("reject")} disabled={deciding}>
            {common.reject}
          </Button>
          <Button
            onClick={() => void run.decide("accept")}
            disabled={deciding}
            data-testid={`${testId}-accept`}
          >
            {deciding && <Loader2 className="animate-spin" />}
            {common.accept}
          </Button>
        </>
      )}
      {decision === "accepted" && (
        <Button onClick={() => void run.rebaseline()} data-testid={`${testId}-rebaseline`}>
          {common.rebaseline}
        </Button>
      )}
    </>
  );
}
