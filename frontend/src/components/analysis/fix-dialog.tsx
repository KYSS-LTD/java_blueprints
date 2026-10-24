"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Loader2, Wrench } from "lucide-react";
import { useMemo, useState } from "react";

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
import { Skeleton } from "@/components/ui/skeleton";
import { FileDiffs } from "@/components/workspace/file-diffs";
import {
  CandidateDecision,
  CandidateOutcome,
  type CandidateTexts,
  useCandidateRun,
} from "@/components/workspace/candidate-run";
import { applyFix, previewFix } from "@/lib/api/actions";
import type { Finding } from "@/lib/analysis/view";
import { ru } from "@/lib/i18n/ru";
import { cn } from "@/lib/utils";

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

const TEXTS = (ruleId: string): CandidateTexts => ({
  runLabel: `${t.title} · ${ruleId}`,
  applyFailed: t.applyFailed,
  outcome: t.outcome,
  decided: t.decided,
});

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
  const texts = useMemo(() => TEXTS(finding.rule_id), [finding.rule_id]);
  const run = useCandidateRun(projectId, texts, { onBusy, onClose });
  const { head, applied, running } = run;
  // после применения запрос отключается: HEAD сменится, и находки с этим id на новой версии уже не будет
  const preview = useQuery({
    queryKey: ["fixes", projectId, "preview", finding.id],
    queryFn: () => previewFix(projectId, finding.id),
    enabled: head !== undefined && applied === null,
    retry: false,
    gcTime: 0,
  });
  const data = preview.data;
  const preserving = data?.behavior_preserving ?? finding.fix?.behavior_preserving ?? true;

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

      {applied && <CandidateOutcome run={run} testId="fix" />}

      <DialogFooter>
        <CandidateDecision run={run} testId="fix" />
        <Button variant="outline" onClick={onClose} disabled={running}>
          {t.close}
        </Button>
        {!applied && (
          <Button
            onClick={() => void run.apply((base) => applyFix(projectId, finding.id, base))}
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
