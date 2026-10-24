"use client";

import { useQuery } from "@tanstack/react-query";
import { Boxes, Loader2 } from "lucide-react";
import { useState } from "react";

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
import {
  CandidateDecision,
  CandidateOutcome,
  type CandidateTexts,
  useCandidateRun,
} from "@/components/workspace/candidate-run";
import { FileDiffs } from "@/components/workspace/file-diffs";
import { type DecomposePlan, applyDecompose, previewDecompose } from "@/lib/api/actions";
import { isCluster } from "@/lib/decompose/plan-edit";
import { ru } from "@/lib/i18n/ru";

const t = ru.decomposeDialog;

const TEXTS: CandidateTexts = {
  runLabel: t.runLabel,
  applyFailed: t.applyFailed,
  outcome: t.outcome,
  decided: t.decided,
};

type Props = {
  projectId: number;
  /** План, который применяется; `null` — диалог закрыт. */
  plan: DecomposePlan | null;
  onClose: () => void;
};

/** Предпросмотр декомпозиции (новые файлы и diff ядра, предупреждения, результат компиляции) и применение. */
export function ApplyDialog({ projectId, plan, onClose }: Props) {
  const [busy, setBusy] = useState(false);
  return (
    <Dialog open={plan !== null} onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent
        className="max-h-[90vh] overflow-y-auto sm:max-w-5xl"
        data-testid="decompose-dialog"
      >
        {/* тело монтируется при каждом открытии: прежний результат не показывается */}
        {plan && <ApplyBody projectId={projectId} plan={plan} onBusy={setBusy} onClose={onClose} />}
      </DialogContent>
    </Dialog>
  );
}

function ApplyBody({
  projectId,
  plan,
  onBusy,
  onClose,
}: {
  projectId: number;
  plan: DecomposePlan;
  onBusy: (busy: boolean) => void;
  onClose: () => void;
}) {
  const run = useCandidateRun(projectId, TEXTS, { onBusy, onClose });
  const { head, applied, running } = run;
  const preview = useQuery({
    queryKey: ["decompose", projectId, "preview", head, plan],
    queryFn: () => previewDecompose(projectId, head!, plan),
    enabled: head !== undefined && applied === null,
    retry: false,
    gcTime: 0,
  });
  const data = preview.data;
  const created = plan.classes.filter((cls) => isCluster(cls) || cls.role === "model");
  const broken = data?.warnings.some((warning) => warning.includes("не компилируется")) ?? false;

  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex flex-wrap items-center gap-2">
          <Boxes className="size-4" aria-hidden />
          {t.title}
          <Badge variant="outline" className="font-mono text-[10px]">
            REF-04
          </Badge>
        </DialogTitle>
        <DialogDescription>{t.description}</DialogDescription>
      </DialogHeader>

      {preview.isLoading && <Skeleton className="h-40" aria-label={t.loading} />}
      {preview.error && (
        <p className="text-sm text-destructive" role="alert">
          {t.failed}: {preview.error.message}
        </p>
      )}

      {data && (
        <div className="space-y-3 text-sm">
          <section aria-label={t.classes}>
            <h3 className="mb-1 text-xs font-semibold text-muted-foreground">{t.classes}</h3>
            <ul className="flex flex-wrap gap-1.5" data-testid="decompose-files">
              {created.map((cls) => (
                <li key={cls.file}>
                  <Badge variant="secondary" className="font-mono text-[11px]">
                    {cls.file}
                  </Badge>
                </li>
              ))}
            </ul>
          </section>

          {data.warnings.length > 0 && (
            <section aria-label={t.warnings} data-testid="decompose-preview-warnings">
              <h3 className="mb-1 text-xs font-semibold text-muted-foreground">{t.warnings}</h3>
              <ul className="list-disc space-y-0.5 pl-4 text-xs text-severity-medium">
                {data.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </section>
          )}

          <section aria-label={t.diff}>
            <h3 className="mb-1 text-xs font-semibold text-muted-foreground">{t.diff}</h3>
            {data.diff.length === 0 ? (
              <p className="text-xs text-muted-foreground">{t.noDiff}</p>
            ) : (
              <FileDiffs diffs={data.diff} height="h-80" />
            )}
          </section>
        </div>
      )}

      {applied && <CandidateOutcome run={run} testId="decompose" />}

      <DialogFooter>
        <CandidateDecision run={run} testId="decompose" />
        <Button variant="outline" onClick={onClose} disabled={running}>
          {t.close}
        </Button>
        {!applied && (
          <Button
            onClick={() => void run.apply((base) => applyDecompose(projectId, base, plan))}
            disabled={!data || broken || running || head === undefined}
            data-testid="decompose-confirm"
          >
            {running ? <Loader2 className="animate-spin" /> : <Boxes />}
            {running ? t.applying : t.apply}
          </Button>
        )}
      </DialogFooter>
    </>
  );
}
