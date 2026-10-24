"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  Download,
  FlaskConical,
  Loader2,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";
import { Fragment, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { FileDiffs } from "@/components/workspace/file-diffs";
import { GenerateDialog } from "@/components/workspace/generate-dialog";
import { ScenarioDialog } from "@/components/workspace/scenario-dialog";
import {
  deleteScenario,
  type FileDiff,
  refreshProject,
  type Scenario,
  updateScenario,
} from "@/lib/api/actions";
import { exportUrl } from "@/lib/api/projects";
import { projectQuery, scenariosQuery } from "@/lib/api/queries";
import { flushDrafts } from "@/lib/edit/session";
import { ru } from "@/lib/i18n/ru";
import { isRunning } from "@/lib/run/run-state";
import {
  buildRows,
  diffHeadline,
  summarize,
  type TestItem,
  type TestRow,
  type Verdict,
} from "@/lib/run/tests-view";
import { cn } from "@/lib/utils";
import { type RunEntry, useRunStore } from "@/stores/run-store";

const t = ru.tests;

const VERDICT_STYLE: Record<Verdict, string> = {
  PASS: "border-node-return/50 text-node-return",
  FAIL: "border-severity-high/50 text-severity-high",
  ERROR: "border-severity-high/50 text-severity-high",
  TIMEOUT: "border-severity-medium/50 text-severity-medium",
};

const TEST_KINDS = new Set<RunEntry["kind"]>(["test", "gate", "baseline"]);

/** Последний прогон проверки (тесты, gate или запись эталона). */
export function latestCheck(
  order: number[],
  entries: Record<number, RunEntry>,
): RunEntry | undefined {
  for (let index = order.length - 1; index >= 0; index -= 1) {
    const entry = entries[order[index]];
    if (entry && TEST_KINDS.has(entry.kind)) return entry;
  }
  return undefined;
}

function diffsOf(item: TestItem | null): FileDiff[] {
  const diff = item?.diff;
  if (!diff) return [];
  const result: FileDiff[] = [];
  for (const [path, change] of [
    [t.diff.stdout, diff.stdout],
    [t.diff.stderr, diff.stderr],
  ] as const) {
    if (change)
      result.push({
        path,
        status: "modified",
        unified: change.unified,
        before: change.expected,
        after: change.actual,
      });
  }
  return result;
}

function DiffDetails({ item }: { item: TestItem }) {
  const diffs = diffsOf(item);
  const { diff } = item;
  return (
    <div className="space-y-2 p-3 text-xs" data-testid="test-diff">
      <p className="font-medium">{t.diff.title}</p>
      {diff?.exit_code && (
        <p>
          {t.diff.exit}: {t.diff.expected} <code>{String(diff.exit_code.expected)}</code> →{" "}
          {t.diff.actual} <code>{String(diff.exit_code.actual)}</code>
        </p>
      )}
      {diff?.exception && (
        <p>
          {t.diff.exception}: {t.diff.expected}{" "}
          <code>{diff.exception.expected ?? t.diff.none}</code> → {t.diff.actual}{" "}
          <code>{diff.exception.actual ?? t.diff.none}</code>
        </p>
      )}
      {diffs.length > 0 && <FileDiffs diffs={diffs} language="plaintext" height="h-56" />}
    </div>
  );
}

function ResultCell({ row }: { row: TestRow }) {
  const { item, scenario } = row;
  if (item) {
    return (
      <div className="flex flex-col items-start gap-0.5">
        <Badge variant="outline" className={cn("text-[10px]", VERDICT_STYLE[item.verdict])}>
          {t.verdict[item.verdict]}
        </Badge>
        {item.verdict !== "PASS" && diffHeadline(item) && (
          <span
            className="max-w-48 truncate text-[10px] text-muted-foreground"
            title={diffHeadline(item) ?? undefined}
          >
            {diffHeadline(item)}
          </span>
        )}
      </div>
    );
  }
  return (
    <Badge variant="outline" className="text-[10px] text-muted-foreground">
      {t.baselineState[scenario.baseline.state]}
    </Badge>
  );
}

function ScenarioRow({
  row,
  open,
  onToggle,
  onEdit,
  onToggleEnabled,
  onDelete,
}: {
  row: TestRow;
  open: boolean;
  onToggle: () => void;
  onEdit: () => void;
  onToggleEnabled: (enabled: boolean) => void;
  onDelete: () => void;
}) {
  const { scenario, item } = row;
  const [confirming, setConfirming] = useState(false);
  const expandable = item !== null && item.verdict !== "PASS";
  const time = item?.duration_ms ?? scenario.baseline.duration_ms;
  return (
    <Fragment>
      <TableRow
        data-testid="test-row"
        data-verdict={item?.verdict ?? "none"}
        className={cn(!scenario.enabled && "opacity-50")}
      >
        <TableCell className="text-xs font-medium">
          <button
            type="button"
            className="flex items-center gap-1 text-left"
            onClick={onToggle}
            disabled={!expandable}
            aria-expanded={expandable ? open : undefined}
          >
            {expandable ? (
              open ? (
                <ChevronDown className="size-3" />
              ) : (
                <ChevronRight className="size-3" />
              )
            ) : (
              <span className="w-3" />
            )}
            <span className="truncate">{scenario.name}</span>
          </button>
          <span className="ml-4 text-[10px] font-normal text-muted-foreground">
            {t.source[scenario.source]}
          </span>
        </TableCell>
        <TableCell>
          <ResultCell row={row} />
        </TableCell>
        <TableCell className="text-right font-mono text-xs">
          {time === null ? "—" : `${time} мс`}
        </TableCell>
        <TableCell className="text-right">
          <div className="flex items-center justify-end gap-1">
            <Switch
              size="sm"
              checked={scenario.enabled}
              onCheckedChange={onToggleEnabled}
              aria-label={`${t.enable}: ${scenario.name}`}
            />
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={onEdit}
              aria-label={`${t.editScenario}: ${scenario.name}`}
            >
              <Pencil />
            </Button>
            {confirming ? (
              <Button
                variant="destructive"
                size="xs"
                onClick={onDelete}
                onBlur={() => setConfirming(false)}
                autoFocus
              >
                {t.deleteScenario}?
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => setConfirming(true)}
                aria-label={`${t.deleteScenario}: ${scenario.name}`}
              >
                <Trash2 />
              </Button>
            )}
          </div>
        </TableCell>
      </TableRow>
      {open && item && (
        <TableRow>
          <TableCell colSpan={4} className="bg-muted/30 p-0">
            <DiffDetails item={item} />
          </TableCell>
        </TableRow>
      )}
    </Fragment>
  );
}

function GateStages({ entry }: { entry: RunEntry }) {
  const stages = new Map(entry.state.gate.map((g) => [g.stage, g]));
  if (stages.size === 0) return null;
  return (
    <ol className="flex flex-wrap gap-1 px-3 pt-2 text-[10px]" aria-label={t.started.gate}>
      {[...stages.values()].map((stage) => (
        <li key={stage.stage} title={stage.details ?? undefined}>
          <Badge
            variant="outline"
            className={cn(
              stage.status === "passed" && "border-node-return/50 text-node-return",
              stage.status === "failed" && "border-severity-high/50 text-severity-high",
            )}
          >
            {t.gateStages[stage.stage]}: {t.gateStatus[stage.status]}
          </Badge>
        </li>
      ))}
    </ol>
  );
}

export function TestsPanel({ projectId }: { projectId: number }) {
  const queryClient = useQueryClient();
  const project = useQuery(projectQuery(projectId));
  const scenarios = useQuery(scenariosQuery(projectId));
  const entry = useRunStore((s) => latestCheck(s.order, s.entries));
  const [editing, setEditing] = useState<Scenario | null>(null);
  const [creating, setCreating] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [opened, setOpened] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);

  const running = entry ? isRunning(entry.state) : false;
  const done = entry?.state.done ?? false;
  const finishedId = done ? entry?.id : undefined;
  // по окончании прогона обновляются сценарии, значок поведения и версии
  useEffect(() => {
    if (finishedId !== undefined) void refreshProject(queryClient, projectId);
  }, [finishedId, queryClient, projectId]);

  const items = useMemo<TestItem[]>(
    () => (entry && entry.kind !== "baseline" ? entry.state.items : []),
    [entry],
  );
  const list = useMemo(() => scenarios.data?.scenarios ?? [], [scenarios.data]);
  const rows = useMemo(() => buildRows(list, items), [list, items]);
  const enabledCount = list.filter((s) => s.enabled).length;

  const gate = project.data?.gate;
  const live = summarize(items);
  const passed = items.length > 0 ? live.passed : (gate?.passed ?? 0);
  const total = items.length > 0 ? live.total : (gate?.total ?? 0);
  const progress = entry?.state.progress;
  const message =
    entry?.state.result?.message ??
    (total > 0 && !running
      ? `${live.failed === 0 && items.length > 0 ? t.allPassed : t.someFailed} — ${passed} ${t.countOf} ${total}`
      : null);

  const start = async (kind: "test" | "gate" | "baseline") => {
    setStarting(true);
    try {
      await flushDrafts(queryClient, projectId);
      setOpened(null);
      await useRunStore.getState().start(projectId, { kind }, t.started[kind]);
    } catch (error) {
      toast.error(t.failedToStart, {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setStarting(false);
    }
  };

  const toggleEnabled = async (scenario: Scenario, enabled: boolean) => {
    try {
      await updateScenario(projectId, scenario.id, { enabled });
      await refreshProject(queryClient, projectId);
    } catch (error) {
      toast.error(ru.scenarioDialog.failed, {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  };

  const remove = async (scenario: Scenario) => {
    try {
      await deleteScenario(projectId, scenario.id);
      toast.success(t.deleted);
      await refreshProject(queryClient, projectId);
    } catch (error) {
      toast.error(t.deleteFailed, {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  };

  const busy = starting || running;

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label={t.title}>
      <div className="flex flex-wrap items-center gap-2 border-b px-3 py-1.5">
        <h2 className="text-sm font-semibold">{t.title}</h2>
        {message && (
          <span className="text-xs text-muted-foreground" role="status" data-testid="tests-message">
            {message}
          </span>
        )}
        <div className="ml-auto flex flex-wrap gap-1">
          <Button variant="outline" size="xs" onClick={() => setCreating(true)}>
            <Plus /> {t.addScenario}
          </Button>
          <Button variant="outline" size="xs" onClick={() => setGenerating(true)} disabled={busy}>
            {t.generate}
          </Button>
          <Button
            variant="outline"
            size="xs"
            onClick={() => void start("baseline")}
            disabled={busy || list.length === 0}
          >
            {t.baseline}
          </Button>
          <Button
            variant="outline"
            size="xs"
            onClick={() => void start("gate")}
            disabled={busy || enabledCount === 0}
          >
            {t.runGate}
          </Button>
          <Button
            size="xs"
            onClick={() => void start("test")}
            disabled={busy || enabledCount === 0}
            data-testid="run-tests"
          >
            {busy ? <Loader2 className="animate-spin" /> : <FlaskConical />} {t.runScenarios}
          </Button>
          <Button variant="outline" size="xs" asChild>
            <a href={exportUrl(projectId, "report")} download>
              <Download /> {t.report}
            </a>
          </Button>
        </div>
      </div>

      {entry && entry.kind === "gate" && <GateStages entry={entry} />}

      {(running || total > 0) && (
        <div className="px-3 pt-2">
          <Progress
            value={
              running && progress
                ? (progress.done / Math.max(progress.total, 1)) * 100
                : total > 0
                  ? (passed / total) * 100
                  : 0
            }
            aria-label={running ? t.running : `${passed} ${t.countOf} ${total}`}
          />
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {scenarios.isLoading && <Skeleton className="m-3 h-20" />}
        {!scenarios.isLoading && list.length === 0 && (
          <div className="flex flex-col items-center gap-1 p-6 text-center text-sm text-muted-foreground">
            <FlaskConical className="size-6" aria-hidden />
            <p>{t.empty}</p>
            <p className="text-xs">{t.emptyHint}</p>
          </div>
        )}
        {list.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t.columns.name}</TableHead>
                <TableHead>{t.columns.result}</TableHead>
                <TableHead className="text-right">{t.columns.time}</TableHead>
                <TableHead className="text-right">{t.columns.actions}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <ScenarioRow
                  key={row.scenario.id}
                  row={row}
                  open={opened === row.scenario.id}
                  onToggle={() =>
                    setOpened((current) => (current === row.scenario.id ? null : row.scenario.id))
                  }
                  onEdit={() => setEditing(row.scenario)}
                  onToggleEnabled={(enabled) => void toggleEnabled(row.scenario, enabled)}
                  onDelete={() => void remove(row.scenario)}
                />
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      <ScenarioDialog projectId={projectId} open={creating} onOpenChange={setCreating} />
      <ScenarioDialog
        projectId={projectId}
        open={editing !== null}
        onOpenChange={(open) => !open && setEditing(null)}
        scenario={editing}
      />
      <GenerateDialog projectId={projectId} open={generating} onOpenChange={setGenerating} />
    </section>
  );
}
