"use client";

import { useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { GrowthChart } from "@/components/workspace/growth-chart";
import { verifyFinding } from "@/lib/api/actions";
import {
  type Finding,
  formatK,
  readVerify,
  type VerifyResult,
  verifyFromEvidence,
} from "@/lib/analysis/view";
import { flushDrafts } from "@/lib/edit/session";
import { ru } from "@/lib/i18n/ru";
import { isInProgress } from "@/lib/run/run-state";
import { useRunEvents } from "@/lib/run/use-run-events";
import { useRunStore } from "@/stores/run-store";

const t = ru.benchmark;
const ta = ru.analysis;

function VerifyView({ result }: { result: VerifyResult }) {
  const reach =
    result.reachedBy !== null && result.scenarios !== null
      ? result.reachedBy > 0
        ? ta.verifyReach
            .replace("{reached}", String(result.reachedBy))
            .replace("{total}", String(result.scenarios))
        : ta.verifyUnconfirmed
      : null;
  return (
    <div className="space-y-2" data-testid="verify-result">
      {result.message && <p className="font-medium">{result.message}</p>}
      {reach && <p>{reach}</p>}
      {result.growthK !== null && (
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span>
            {t.k}: <code className="rounded bg-muted px-1.5 py-0.5">{formatK(result.growthK)}</code>
          </span>
          {result.predicted && (
            <span className="text-muted-foreground">
              {ta.growthStatic}: <code>{result.predicted}</code>
            </span>
          )}
        </p>
      )}
      {result.samples.length > 1 && (
        <>
          <p className="text-muted-foreground">
            {ta.verifiedOn} {result.samples.map((s) => s.size).join(", ")}
          </p>
          <GrowthChart samples={result.samples} />
        </>
      )}
      {result.warnings.length > 0 && (
        <ul className="space-y-0.5 text-severity-medium" aria-label={ru.generateDialog.warnings}>
          {result.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** «Проверить» у находки: динамические доказательства — рост на масштабированных входах или достижимость места. */
export function VerifyBlock({ projectId, finding }: { projectId: number; finding: Finding }) {
  const queryClient = useQueryClient();
  const [runId, setRunId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);
  const state = useRunEvents(runId);
  const running = starting || (runId !== null && isInProgress(state));

  // итог запуска сохраняется у находки (`evidence.dynamic`): по окончании анализ перечитывается
  useEffect(() => {
    if (state.done) void queryClient.invalidateQueries({ queryKey: ["analysis", projectId] });
  }, [state.done, queryClient, projectId]);

  const live = useMemo(
    () => (state.done && state.status === "done" ? readVerify(state.result) : null),
    [state.done, state.status, state.result],
  );
  const stored = finding.evidence.dynamic;
  const result =
    live ?? (stored ? verifyFromEvidence(stored, finding.complexity?.current ?? null) : null);
  const failure =
    state.error?.message ??
    (state.done && state.status === "failed" ? state.result?.message : null);

  const start = async () => {
    setStarting(true);
    try {
      await flushDrafts(queryClient, projectId);
      const id = await verifyFinding(projectId, finding.id);
      useRunStore
        .getState()
        .track(id, { label: `${t.verify} · ${finding.rule_id}`, kind: "verify" });
      setRunId(id);
    } catch (error) {
      toast.error(t.verifyFailed, {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="space-y-2 border-t pt-2 text-xs" aria-live="polite">
      <div className="flex items-center gap-2">
        <Button
          size="xs"
          variant="outline"
          onClick={() => void start()}
          disabled={running}
          data-testid="verify-button"
        >
          {running ? <Loader2 className="animate-spin" /> : <FlaskConical />}
          {running ? t.verifying : t.verify}
        </Button>
        {result && !running && <span className="text-muted-foreground">{t.verified}</span>}
      </div>
      {failure && <p className="text-destructive">{failure}</p>}
      {result && !running && <VerifyView result={result} />}
    </div>
  );
}
