"use client";

import {
  ArrowRight,
  Boxes,
  CircleAlert,
  Loader2,
  Minus,
  Plus,
  RotateCcw,
  TriangleAlert,
} from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { ApplyDialog } from "@/components/decompose/apply-dialog";
import { ClassCard, type CardHandlers, NewClassDrop } from "@/components/decompose/class-card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { DecomposePlan } from "@/lib/api/actions";
import {
  isNameFree,
  isCluster,
  moveMember,
  renameClass,
  shrinkPercent,
  summarize,
} from "@/lib/decompose/plan-edit";
import { useDecomposePlan } from "@/lib/decompose/use-plan";
import { ru } from "@/lib/i18n/ru";
import { cn } from "@/lib/utils";

const t = ru.decompose;

const MIN_CLASSES = 2;
const MAX_CLASSES = 8;
const DEFAULT_CLASSES = 6;

function Metric({
  label,
  value,
  note,
  testId,
}: {
  label: string;
  value: string;
  note?: string;
  testId?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-mono" data-testid={testId}>
        {value}
        {note && <span className="ml-1.5 text-xs text-node-return">{note}</span>}
      </dd>
    </div>
  );
}

/** «До»: монолит одним классом и его метрики. */
function BeforePanel({ plan }: { plan: DecomposePlan }) {
  const core = plan.classes.find((cls) => cls.role === "core");
  const total = plan.classes
    .filter((cls) => cls.role !== "model")
    .reduce((n, cls) => n + cls.members.length, 0);
  return (
    <section
      className="space-y-3 rounded-lg border bg-card p-3"
      aria-label={t.before}
      data-testid="decompose-before"
    >
      <h3 className="text-sm font-semibold">{t.before}</h3>
      <div>
        <p className="font-mono text-sm font-semibold">{core?.name}</p>
        <p className="text-[11px] text-muted-foreground">
          <code>{core?.file}</code>
        </p>
      </div>
      <dl className="space-y-1">
        <Metric label={t.lines} value={String(plan.metrics.before.lines)} testId="before-lines" />
        <Metric label={t.members} value={String(total)} />
        <Metric
          label={t.lcom4}
          value={plan.metrics.before.lcom4.toFixed(1)}
          testId="before-lcom4"
        />
      </dl>
      <p className="text-xs text-muted-foreground">{t.coreHint}</p>
    </section>
  );
}

/** «После»: итоговые метрики плана над карточками классов. */
function AfterSummary({ plan }: { plan: DecomposePlan }) {
  const { clusters } = summarize(plan);
  const change = shrinkPercent(plan.metrics.before.lines, plan.metrics.after.lines);
  return (
    <dl className="grid gap-x-6 gap-y-1 @lg:grid-cols-3" data-testid="decompose-after-summary">
      <Metric
        label={t.linesInCore}
        value={String(plan.metrics.after.lines)}
        note={change === 0 ? undefined : `${change > 0 ? "+" : "−"}${Math.abs(change)} %`}
        testId="after-lines"
      />
      <Metric
        label={t.lcom4Average}
        value={plan.metrics.after.lcom4.toFixed(1)}
        testId="after-lcom4"
      />
      <Metric label={t.newClasses} value={String(clusters)} testId="after-classes" />
    </dl>
  );
}

function Links({ plan }: { plan: DecomposePlan }) {
  return (
    <section aria-label={t.links} data-testid="decompose-links">
      <h3 className="mb-1 text-xs font-semibold text-muted-foreground">{t.links}</h3>
      {plan.edges.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t.noLinks}</p>
      ) : (
        <ul className="grid gap-x-6 gap-y-0.5 text-xs @xl:grid-cols-2">
          {plan.edges.map((edge) => (
            <li
              key={`${edge.from}>${edge.to}`}
              className="flex items-center gap-1.5"
              data-testid="decompose-link"
            >
              <code>{edge.from}</code>
              <ArrowRight className="size-3 shrink-0 text-muted-foreground" aria-hidden />
              <code>{edge.to}</code>
              <span className="ml-auto text-muted-foreground">
                {edge.calls} {t.calls}
                {edge.fields > 0 && ` · ${edge.fields} ${t.fields}`}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Вкладка «Декомпозиция»: план «До → После» с переносом методов между классами (docs/09 § 3.7). */
export function DecomposeView({ projectId }: { projectId: number }) {
  const [maxClasses, setMaxClasses] = useState(DEFAULT_CLASSES);
  const options = useMemo(() => ({ max_classes: maxClasses }), [maxClasses]);
  const state = useDecomposePlan(projectId, options);
  const { plan } = state;
  const [dragged, setDragged] = useState<string | null>(null);
  // применяется план, каким он был в момент нажатия: после успеха версия сменится и план построится заново
  const [applying, setApplying] = useState<DecomposePlan | null>(null);

  const handlers: CardHandlers = {
    dragged,
    onDragStart: setDragged,
    onDragEnd: () => setDragged(null),
    onMove: (memberId, target) => {
      setDragged(null);
      if (!plan) return;
      const result = moveMember(plan, memberId, target);
      if (!result) return;
      for (const name of result.removed) toast(`${name}: ${t.removedEmpty}`);
      void state.commit(result.plan);
    },
    onRename: (from, to) => plan && void state.commit(renameClass(plan, from, to)),
    isNameFree: (name, except) => (plan ? isNameFree(plan, name, except) : true),
  };

  const dialog = (
    <ApplyDialog projectId={projectId} plan={applying} onClose={() => setApplying(null)} />
  );

  if (state.error) {
    return (
      <div className="p-4">
        <Alert variant="destructive">
          <CircleAlert />
          <AlertTitle>{t.failed}</AlertTitle>
          <AlertDescription>{state.error.message}</AlertDescription>
        </Alert>
        {dialog}
      </div>
    );
  }

  const clusters = plan?.classes.filter(isCluster) ?? [];
  const detached = plan?.classes.filter((cls) => cls.role === "model") ?? [];
  const core = plan?.classes.find((cls) => cls.role === "core");

  return (
    <div className="@container flex h-full min-h-0 flex-col" data-testid="decompose-view">
      <header className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-2">
        <div className="min-w-0 flex-1">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <Boxes className="size-4" aria-hidden /> {t.heading}
          </h2>
          <p className="max-w-3xl text-xs text-muted-foreground">{t.description}</p>
        </div>
        <div className="flex items-center gap-1.5 text-xs" role="group" aria-label={t.options}>
          <span className="text-muted-foreground">{t.options}</span>
          <Button
            variant="outline"
            size="icon-xs"
            aria-label={t.fewer}
            disabled={maxClasses <= MIN_CLASSES || state.recalculating}
            onClick={() => setMaxClasses((value) => Math.max(MIN_CLASSES, value - 1))}
          >
            <Minus />
          </Button>
          <span className="w-4 text-center font-mono" data-testid="decompose-max">
            {maxClasses}
          </span>
          <Button
            variant="outline"
            size="icon-xs"
            aria-label={t.more}
            disabled={maxClasses >= MAX_CLASSES || state.recalculating}
            onClick={() => setMaxClasses((value) => Math.min(MAX_CLASSES, value + 1))}
          >
            <Plus />
          </Button>
        </div>
        {state.recalculating && (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
            <Loader2 className="size-3.5 animate-spin" aria-hidden /> {t.recalculating}
          </span>
        )}
        {state.edited && (
          <Button variant="ghost" size="sm" onClick={state.reset} data-testid="decompose-reset">
            <RotateCcw /> {t.reset}
          </Button>
        )}
        <Button
          size="sm"
          onClick={() => plan && setApplying(plan)}
          disabled={!plan || clusters.length === 0 || state.recalculating}
          data-testid="decompose-apply"
        >
          <Boxes /> {t.apply}
        </Button>
      </header>

      {state.isLoading || !plan ? (
        <div className="p-4" aria-label={t.loading}>
          <Skeleton className="h-64" />
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <div className="grid gap-4 @3xl:grid-cols-[16rem_1fr]">
            <BeforePanel plan={plan} />
            <div className={cn("space-y-4", state.recalculating && "opacity-80")}>
              <section className="space-y-3 rounded-lg border bg-card/50 p-3" aria-label={t.after}>
                <h3 className="text-sm font-semibold">{t.after}</h3>
                <AfterSummary plan={plan} />
                {plan.warnings.length > 0 && (
                  <Alert data-testid="decompose-warnings">
                    <TriangleAlert />
                    <AlertTitle>{t.warnings}</AlertTitle>
                    <AlertDescription>
                      <ul className="list-disc space-y-0.5 pl-4">
                        {plan.warnings.map((warning) => (
                          <li key={warning}>{warning}</li>
                        ))}
                      </ul>
                    </AlertDescription>
                  </Alert>
                )}
                <div className="grid gap-3 @2xl:grid-cols-2 @6xl:grid-cols-3">
                  {core && <ClassCard plan={plan} cls={core} handlers={handlers} />}
                  {clusters.map((cls) => (
                    <ClassCard key={cls.name} plan={plan} cls={cls} handlers={handlers} />
                  ))}
                  <NewClassDrop handlers={handlers} />
                </div>
                <Links plan={plan} />
                {detached.length > 0 && (
                  <p className="text-xs text-muted-foreground" data-testid="decompose-detached">
                    {t.detached}: {detached.map((cls) => cls.file).join(", ")}
                  </p>
                )}
              </section>
            </div>
          </div>
        </div>
      )}

      {dialog}
    </div>
  );
}
