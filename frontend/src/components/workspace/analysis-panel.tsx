"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { analysisQuery } from "@/lib/api/queries";
import type { components } from "@/lib/api/schema";
import { ru } from "@/lib/i18n/ru";
import { cn } from "@/lib/utils";
import { type RightTab, useWorkspaceStore } from "@/stores/workspace-store";

type Finding = components["schemas"]["Finding"];
type Severity = Finding["severity"];

const t = ru.analysis;

const SEVERITY_STYLE: Record<Severity, string> = {
  high: "bg-severity-high/20 text-severity-high border-severity-high/40",
  medium: "bg-severity-medium/20 text-severity-medium border-severity-medium/40",
  low: "bg-severity-low/20 text-severity-low border-severity-low/40",
};

function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <Badge variant="outline" className={cn("shrink-0 text-[10px]", SEVERITY_STYLE[severity])}>
      {t.severity[severity]}
    </Badge>
  );
}

function useOpenFinding() {
  const openMethod = useWorkspaceStore((s) => s.openMethod);
  const selectNode = useWorkspaceStore((s) => s.selectNode);
  const setActiveFile = useWorkspaceStore((s) => s.setActiveFile);
  return (finding: Finding) => {
    const { location } = finding;
    if (location.method_id) openMethod(location.method_id);
    setActiveFile(location.file);
    selectNode(location.node_id ?? location.method_id ?? finding.id, "findings", {
      file: location.file,
      start_line: location.start_line,
      start_col: 1,
      end_line: location.end_line,
      end_col: 1_000,
    });
  };
}

function FindingCard({ finding, withFix }: { finding: Finding; withFix?: boolean }) {
  const [open, setOpen] = useState(false);
  const openFinding = useOpenFinding();
  const selected = useWorkspaceStore(
    (s) => s.selection !== null && s.selection.nodeId === (finding.location.node_id ?? finding.location.method_id),
  );
  return (
    <li
      className={cn("rounded-lg border bg-card p-3 text-sm", selected && "border-primary ring-1 ring-primary")}
      data-testid="finding-card"
    >
      <button type="button" className="w-full text-left" onClick={() => openFinding(finding)}>
        <div className="flex items-start gap-2">
          <TriangleAlert
            className={cn("mt-0.5 size-4 shrink-0", `text-severity-${finding.severity}`)}
            aria-hidden
          />
          <div className="min-w-0 flex-1">
            <p className="font-medium">{finding.title}</p>
            <p className="text-xs text-muted-foreground">
              {finding.location.file}:{finding.location.start_line} · {finding.rule_id}
            </p>
          </div>
          <SeverityBadge severity={finding.severity} />
        </div>
        <p className="mt-2 text-xs text-muted-foreground">{finding.explanation}</p>
      </button>

      {finding.complexity?.after && (
        <p className="mt-2 flex items-center gap-1.5 text-xs">
          <code className="rounded bg-muted px-1.5 py-0.5">{finding.complexity.current}</code>
          <ArrowRight className="size-3 text-muted-foreground" aria-hidden />
          <code className="rounded bg-node-return/15 px-1.5 py-0.5 text-node-return">{finding.complexity.after}</code>
        </p>
      )}

      <Button variant="link" size="xs" className="mt-1 h-auto p-0 text-xs" onClick={() => setOpen((v) => !v)}>
        {open ? "Скрыть" : t.why}
      </Button>
      {open && (
        <div className="mt-2 space-y-2 border-t pt-2 text-xs">
          <p>
            <b>{t.risk}:</b> {finding.risk}
          </p>
          <p>
            <b>{t.recommendation}:</b> {finding.recommendation}
          </p>
          {finding.complexity?.contributions.map((c) => (
            <p key={c.node_id + c.why} className="text-muted-foreground">
              × {c.factor} — {c.why}
            </p>
          ))}
        </div>
      )}

      {withFix && finding.fix && (
        <div className="mt-2 flex items-center justify-between gap-2 border-t pt-2">
          <span className="text-xs text-muted-foreground">
            {finding.fix.fix_id}
            {finding.fix.behavior_preserving ? " · поведение сохраняется" : ""}
          </span>
          <Button size="xs" variant="secondary" disabled title={t.soon}>
            Применить
          </Button>
        </div>
      )}
    </li>
  );
}

const SEVERITIES: Severity[] = ["high", "medium", "low"];

export function AnalysisPanel({ projectId }: { projectId: number }) {
  const analysis = useQuery(analysisQuery(projectId));
  const rightTab = useWorkspaceStore((s) => s.rightTab);
  const setRightTab = useWorkspaceStore((s) => s.setRightTab);
  const [severities, setSeverities] = useState<Set<Severity>>(new Set(SEVERITIES));

  const findings = useMemo(
    () => (analysis.data?.findings ?? []).filter((f) => severities.has(f.severity)),
    [analysis.data, severities],
  );
  const fixable = useMemo(() => findings.filter((f) => f.fix?.available), [findings]);
  const data = analysis.data;

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label={t.title}>
      <div className="flex items-center border-b px-3 py-2">
        <h2 className="text-sm font-semibold">{t.title}</h2>
      </div>

      {analysis.isLoading && <Skeleton className="m-3 h-40" />}
      {analysis.error && <p className="p-3 text-xs text-destructive">{t.loadFailed}: {analysis.error.message}</p>}

      {data && (
        <Tabs
          value={rightTab}
          onValueChange={(value) => setRightTab(value as RightTab)}
          className="min-h-0 flex-1 gap-0"
        >
          <TabsList className="mx-2 mt-2 h-8 w-auto">
            <TabsTrigger value="problems" className="text-xs">
              {t.problems} ({findings.length})
            </TabsTrigger>
            <TabsTrigger value="recommendations" className="text-xs">
              {t.recommendations} ({fixable.length})
            </TabsTrigger>
            <TabsTrigger value="metrics" className="text-xs">
              {t.metrics}
            </TabsTrigger>
          </TabsList>

          {rightTab !== "metrics" && (
            <div className="flex items-center gap-1 px-2 pt-2" role="group" aria-label="Важность">
              {SEVERITIES.map((severity) => (
                <button
                  key={severity}
                  type="button"
                  aria-pressed={severities.has(severity)}
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-[10px] transition-opacity",
                    SEVERITY_STYLE[severity],
                    !severities.has(severity) && "opacity-40",
                  )}
                  onClick={() =>
                    setSeverities((current) => {
                      const next = new Set(current);
                      if (next.has(severity)) next.delete(severity);
                      else next.add(severity);
                      return next;
                    })
                  }
                >
                  {t.severity[severity]} ({data.findings.filter((f) => f.severity === severity).length})
                </button>
              ))}
            </div>
          )}

          <ScrollArea className="min-h-0 flex-1">
            <TabsContent value="problems" className="p-2">
              {findings.length === 0 && <p className="p-2 text-sm text-muted-foreground">{t.empty}</p>}
              <ul className="space-y-2">
                {findings.map((finding) => (
                  <FindingCard key={finding.id} finding={finding} />
                ))}
              </ul>
            </TabsContent>
            <TabsContent value="recommendations" className="p-2">
              {fixable.length === 0 && <p className="p-2 text-sm text-muted-foreground">{t.empty}</p>}
              <ul className="space-y-2">
                {fixable.map((finding) => (
                  <FindingCard key={finding.id} finding={finding} withFix />
                ))}
              </ul>
            </TabsContent>
            <TabsContent value="metrics" className="p-3">
              <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1.5 text-sm">
                <dt className="text-muted-foreground">{t.lines}</dt>
                <dd className="text-right font-mono">{data.metrics.totals.code_lines}</dd>
                <dt className="text-muted-foreground">{t.methods}</dt>
                <dd className="text-right font-mono">{data.metrics.totals.methods}</dd>
                <dt className="text-muted-foreground">{t.cyclomatic}</dt>
                <dd className="text-right font-mono">
                  {data.metrics.totals.cyclomatic_avg.toFixed(1)} / max {data.metrics.totals.cyclomatic_max}
                </dd>
                <dt className="text-muted-foreground">Дублирование</dt>
                <dd className="text-right font-mono">{data.metrics.totals.duplication_percent}%</dd>
              </dl>
              <p className="mt-3 text-xs text-muted-foreground">{t.metricsSoon}</p>
            </TabsContent>
          </ScrollArea>

          <div className="space-y-1 border-t px-3 py-2 text-xs">
            <p className="flex items-center gap-2">
              <span className="text-muted-foreground">{t.complexity}:</span>
              <span>{t.current}</span>
              <code className="rounded bg-muted px-1.5 py-0.5">{data.complexity.current}</code>
              {data.complexity.optimized && (
                <>
                  <ArrowRight className="size-3 text-muted-foreground" aria-hidden />
                  <code className="rounded bg-node-return/15 px-1.5 py-0.5 text-node-return">
                    {data.complexity.optimized}
                  </code>
                </>
              )}
            </p>
            <p className="text-muted-foreground">
              {t.speedup}: {t.notMeasured}
            </p>
          </div>
        </Tabs>
      )}
    </section>
  );
}
