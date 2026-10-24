"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Check, Loader2, Wrench, X } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { PreconditionList } from "@/components/analysis/precondition-list";
import { Badge } from "@/components/ui/badge";
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
import { Skeleton } from "@/components/ui/skeleton";
import { FileDiffs } from "@/components/workspace/file-diffs";
import { GateStages } from "@/components/workspace/gate-stages";
import {
  acceptCandidate,
  applyFix,
  previewFix,
  refreshProject,
  rejectCandidate,
} from "@/lib/api/actions";
import { projectQuery } from "@/lib/api/queries";
import { type Finding, candidateState } from "@/lib/analysis/view";
import { flushDrafts } from "@/lib/edit/session";
import { useEditLock } from "@/lib/edit/use-edit-lock";
import { ru } from "@/lib/i18n/ru";
import { isInProgress } from "@/lib/run/run-state";
import { diffHeadline } from "@/lib/run/tests-view";
import { useRunEvents } from "@/lib/run/use-run-events";
import { cn } from "@/lib/utils";
import { useRunStore } from "@/stores/run-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

const t = ru.fixDialog;

type Props = {
  projectId: number;
  /** Находка, исправление которой показывается; `null` — диалог закрыт. */
  finding: Finding | null;
  onClose: () => void;
};

/** Предпросмотр автоисправления (diff, предусловия, эффект) и применение со стадиями проверки поведения. */
export function FixDialog({ projectId, finding, onClose }: Props) {
  const [busy, setBusy] = useState(false);
  return (
    <Dialog open={finding !== null} onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl" data-testid="fix-dialog">
        {/* тело монтируется при каждом открытии: прежний результат не показывается */}
        {finding && (
          <FixBody projectId={projectId} finding={finding} onBusy={setBusy} onClose={onClose} />
        )}
      </DialogContent>
    </Dialog>
  );
}

function FixBody({
  projectId,
  finding,
  onBusy,
  onClose,
}: {
  projectId: number;
  finding: Finding;
  onBusy: (busy: boolean) => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const project = useQuery(projectQuery(projectId));
  const head = project.data?.head_version_id;
  const [applied, setApplied] = useState<{ candidateId: number; runId: number } | null>(null);
  // после применения запрос отключается: HEAD сменится, и находки с этим id на новой версии уже не будет
  const preview = useQuery({
    queryKey: ["fixes", projectId, "preview", finding.id],
    queryFn: () => previewFix(projectId, finding.id),
    enabled: head !== undefined && applied === null,
    retry: false,
    gcTime: 0,
  });
  const [starting, setStarting] = useState(false);
  const [decision, setDecision] = useState<"accepted" | "rejected" | null>(null);
  const [deciding, setDeciding] = useState(false);
  const state = useRunEvents(applied?.runId ?? null);
  const running = starting || (applied !== null && isInProgress(state));
  const candidate = state.done ? candidateState(state.result) : null;
  const data = preview.data;

  useEditLock(running);
  useEffect(() => {
    onBusy(running);
  }, [running, onBusy]);

  useEffect(() => {
    if (!state.done) return;
    void refreshProject(queryClient, projectId);
    if (candidate === "promoted") toast.success(t.outcome.promoted);
  }, [state.done, candidate, queryClient, projectId]);

  const apply = async () => {
    if (head === undefined) return;
    setStarting(true);
    try {
      await flushDrafts(queryClient, projectId);
      const result = await applyFix(projectId, finding.id, head);
      useRunStore.getState().track(result.run_id, {
        label: `${t.title} · ${finding.rule_id}`,
        kind: "gate",
      });
      setApplied({ candidateId: result.candidate_version_id, runId: result.run_id });
    } catch (error) {
      toast.error(t.applyFailed, {
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
      toast(kind === "accept" ? t.accepted : t.rejectedToast);
      await refreshProject(queryClient, projectId);
    } catch (error) {
      toast.error(t.decisionFailed, {
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
      toast(t.rebaselineStarted);
      onClose();
    } catch (error) {
      toast.error(ru.tests.failedToStart, {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  };

  const failed = state.items.filter((item) => item.verdict !== "PASS");
  const preserving = data?.behavior_preserving ?? finding.fix?.behavior_preserving ?? true;
  const finished = applied !== null && state.done;
  // зелёный итог: версия стала текущей — сама после проверки или по решению пользователя
  const good = decision === "accepted" || (decision === null && candidate === "promoted");

  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex flex-wrap items-center gap-2">
          <Wrench className="size-4" aria-hidden />
          {data?.title ?? t.title}
          <Badge variant="outline" className="font-mono text-[10px]">
            {finding.fix?.fix_id ?? finding.rule_id}
          </Badge>
        </DialogTitle>
        <DialogDescription>{data?.summary ?? t.description}</DialogDescription>
      </DialogHeader>

      {preview.isLoading && <Skeleton className="h-40" aria-label={t.loading} />}
      {preview.error && (
        <div className="space-y-2 text-sm" role="alert">
          <p className="text-destructive">
            {t.failed}: {preview.error.message}
          </p>
          {finding.fix && finding.fix.preconditions.length > 0 && (
            <PreconditionList preconditions={finding.fix.preconditions} />
          )}
        </div>
      )}

      {data && (
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
            <Badge
              variant="outline"
              className={cn(
                preserving
                  ? "border-node-return/50 text-node-return"
                  : "border-severity-medium/50 text-severity-medium",
              )}
            >
              {preserving ? t.behaviorSafe : t.behaviorChanges}
            </Badge>
            {data.effect.complexity_before && (
              <span className="flex items-center gap-1.5" data-testid="fix-effect">
                {t.complexity}:
                <code className="rounded bg-muted px-1.5 py-0.5">
                  {data.effect.complexity_before}
                </code>
                {data.effect.complexity_after && (
                  <>
                    <ArrowRight className="size-3 text-muted-foreground" aria-hidden />
                    <code className="rounded bg-node-return/15 px-1.5 py-0.5 text-node-return">
                      {data.effect.complexity_after}
                    </code>
                  </>
                )}
              </span>
            )}
          </div>
          {data.effect.notes && data.effect.notes !== data.summary && (
            <p className="text-xs text-muted-foreground">{data.effect.notes}</p>
          )}

          <section aria-label={t.preconditions}>
            <h3 className="mb-1 text-xs font-semibold text-muted-foreground">{t.preconditions}</h3>
            <PreconditionList preconditions={data.preconditions} />
          </section>

          <section aria-label={t.diff}>
            <h3 className="mb-1 text-xs font-semibold text-muted-foreground">{t.diff}</h3>
            {data.diff.length === 0 ? (
              <p className="text-xs text-muted-foreground">{t.noDiff}</p>
            ) : (
              <FileDiffs diffs={data.diff} height="h-72" />
            )}
          </section>
        </div>
      )}

      {applied && (
        <div
          className="space-y-2 rounded-md border p-3 text-sm"
          aria-live="polite"
          data-testid="fix-outcome"
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
                data-testid="fix-outcome-message"
                data-candidate={candidate ?? "none"}
                data-decision={decision ?? "none"}
              >
                {good ? (
                  <Check className="mt-0.5 size-4 shrink-0" aria-hidden />
                ) : (
                  <X className="mt-0.5 size-4 shrink-0" aria-hidden />
                )}
                {decision ? t.decided[decision] : t.outcome[candidate ?? "unknown"]}
              </p>
              {state.result?.message && (
                <p className="text-xs text-muted-foreground">{state.result.message}</p>
              )}
              {failed.length > 0 && (
                <div className="space-y-1">
                  <p className="text-xs font-semibold">{t.changedScenarios}</p>
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
                <p className="text-xs text-muted-foreground">{t.rebaselineHint}</p>
              )}
            </>
          )}
        </div>
      )}

      <DialogFooter>
        {candidate === "candidate" && decision === null && (
          <>
            <Button variant="outline" onClick={() => void decide("reject")} disabled={deciding}>
              {t.reject}
            </Button>
            <Button
              onClick={() => void decide("accept")}
              disabled={deciding}
              data-testid="fix-accept"
            >
              {deciding && <Loader2 className="animate-spin" />}
              {t.accept}
            </Button>
          </>
        )}
        {decision === "accepted" && (
          <Button onClick={() => void rebaseline()} data-testid="fix-rebaseline">
            {t.rebaseline}
          </Button>
        )}
        <Button variant="outline" onClick={onClose} disabled={running}>
          {t.close}
        </Button>
        {!applied && (
          <Button
            onClick={() => void apply()}
            disabled={!data || running || head === undefined}
            data-testid="fix-apply"
          >
            {running ? <Loader2 className="animate-spin" /> : <Wrench />}
            {running ? t.applying : t.apply}
          </Button>
        )}
      </DialogFooter>
    </>
  );
}
