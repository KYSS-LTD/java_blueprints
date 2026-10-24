"use client";

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Scissors } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  CandidateDecision,
  CandidateOutcome,
  type CandidateTexts,
  useCandidateRun,
} from "@/components/workspace/candidate-run";
import { FileDiffs } from "@/components/workspace/file-diffs";
import { applyExtract, previewExtract } from "@/lib/api/actions";
import { projectQuery } from "@/lib/api/queries";
import { flushDrafts } from "@/lib/edit/session";
import { useDebouncedValue } from "@/lib/hooks/use-debounced-value";
import { ru } from "@/lib/i18n/ru";
import { type ExtractTarget, useWorkspaceStore } from "@/stores/workspace-store";

const t = ru.extractDialog;

const NAME_DELAY_MS = 350;

const TEXTS: CandidateTexts = {
  runLabel: t.runLabel,
  applyFailed: t.applyFailed,
  outcome: t.outcome,
  decided: t.decided,
};

/** Что отправляется в `extract-method/*`: блоки схемы либо диапазон кода. */
function requestOf(target: ExtractTarget) {
  return "nodeIds" in target ? { node_ids: target.nodeIds } : { range: target.range };
}

/** Диалог «Выделить в метод»: имя, сигнатура, предусловия, diff и применение со стадиями проверки поведения. */
export function ExtractDialog({ projectId }: { projectId: number }) {
  const target = useWorkspaceStore((s) => s.extractTarget);
  const close = useWorkspaceStore((s) => s.closeExtract);
  const [busy, setBusy] = useState(false);
  return (
    <Dialog open={target !== null} onOpenChange={(open) => !open && !busy && close()}>
      <DialogContent
        className="max-h-[90vh] overflow-y-auto sm:max-w-4xl"
        data-testid="extract-dialog"
      >
        {/* тело монтируется при каждом открытии: прежний результат не показывается */}
        {target && (
          <ExtractBody projectId={projectId} target={target} onBusy={setBusy} onClose={close} />
        )}
      </DialogContent>
    </Dialog>
  );
}

function ExtractBody({
  projectId,
  target,
  onBusy,
  onClose,
}: {
  projectId: number;
  target: ExtractTarget;
  onBusy: (busy: boolean) => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const run = useCandidateRun(projectId, TEXTS, { onBusy, onClose });
  const { head, applied, running } = run;
  // номера строк фрагмента относятся к набранному тексту: сначала он сохраняется
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    let cancelled = false;
    flushDrafts(queryClient, projectId)
      .catch(() => undefined)
      .finally(() => !cancelled && setSaved(true));
    return () => {
      cancelled = true;
    };
  }, [queryClient, projectId]);
  const headNow = useQuery(projectQuery(projectId)).data?.head_version_id;

  const [typed, setTyped] = useState<string | null>(null);
  const wanted = typed?.trim() || null;
  const name = useDebouncedValue(wanted, NAME_DELAY_MS);
  const request = useMemo(() => ({ ...requestOf(target), name }), [target, name]);
  const preview = useQuery({
    queryKey: ["extract", projectId, headNow, request],
    queryFn: () => previewExtract(projectId, { ...request, version_id: headNow }),
    enabled: saved && headNow !== undefined && applied === null,
    placeholderData: keepPreviousData,
    retry: false,
    gcTime: 0,
  });
  const data = preview.data;
  const failedChecks = data?.preconditions.filter((p) => !p.passed) ?? [];
  const available = data !== undefined && failedChecks.length === 0 && data.diff.length > 0;
  const shownName = typed ?? data?.name ?? "";
  const renamed = typed !== null && data !== undefined && name !== null && data.name !== name;

  const finish = () => {
    // блоки старой версии после выделения уже другие
    if (applied) useWorkspaceStore.getState().selectNode(null, "edit");
    onClose();
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex flex-wrap items-center gap-2">
          <Scissors className="size-4" aria-hidden />
          {t.title}
          <Badge variant="outline" className="font-mono text-[10px]">
            REF-01
          </Badge>
        </DialogTitle>
        <DialogDescription>{t.description}</DialogDescription>
      </DialogHeader>

      {(!saved || (preview.isLoading && !data)) && (
        <Skeleton className="h-40" aria-label={t.loading} />
      )}
      {preview.error && (
        <p className="text-sm text-destructive" role="alert">
          {t.failed}: {preview.error.message}
        </p>
      )}

      {data && (
        <div className="space-y-3 text-sm">
          <div className="space-y-1">
            <Label htmlFor="extract-name">{t.name}</Label>
            <Input
              id="extract-name"
              value={shownName}
              onChange={(event) => setTyped(event.target.value)}
              disabled={applied !== null}
              spellCheck={false}
              autoComplete="off"
              className="font-mono"
              data-testid="extract-name"
            />
            {renamed && (
              <p className="text-xs text-muted-foreground" data-testid="extract-renamed">
                {t.nameTaken} <code>{data.name}</code>
              </p>
            )}
          </div>

          {data.signature && (
            <section aria-label={t.signature}>
              <h3 className="mb-1 text-xs font-semibold text-muted-foreground">{t.signature}</h3>
              <code
                className="block overflow-x-auto rounded bg-muted px-2 py-1.5 text-xs whitespace-pre"
                data-testid="extract-signature"
              >
                {data.signature}
              </code>
            </section>
          )}

          <section aria-label={t.preconditions}>
            <h3 className="mb-1 text-xs font-semibold text-muted-foreground">{t.preconditions}</h3>
            <PreconditionList preconditions={data.preconditions} />
          </section>

          {data.warnings.length > 0 && (
            <section aria-label={t.warnings} data-testid="extract-warnings">
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
              <p className="text-xs text-muted-foreground" data-testid="extract-unavailable">
                {failedChecks.length > 0 ? t.unavailable : t.noDiff}
              </p>
            ) : (
              <FileDiffs diffs={data.diff} height="h-72" />
            )}
          </section>
        </div>
      )}

      {applied && <CandidateOutcome run={run} testId="extract" />}

      <DialogFooter>
        <CandidateDecision run={run} testId="extract" />
        <Button variant="outline" onClick={finish} disabled={running}>
          {t.close}
        </Button>
        {!applied && (
          <Button
            onClick={() =>
              void run.apply((base) =>
                applyExtract(projectId, base, { ...requestOf(target), name }),
              )
            }
            disabled={
              !available || running || preview.isFetching || wanted !== name || head === undefined
            }
            data-testid="extract-apply"
          >
            {running ? <Loader2 className="animate-spin" /> : <Scissors />}
            {running ? t.applying : t.apply}
          </Button>
        )}
      </DialogFooter>
    </>
  );
}
