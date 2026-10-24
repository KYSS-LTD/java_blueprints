"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useMemo, useState } from "react";

import { FindingCard, SEVERITY_STYLE } from "@/components/analysis/finding-card";
import { FixDialog } from "@/components/analysis/fix-dialog";
import { MetricsTab } from "@/components/analysis/metrics-tab";
import { PerformanceBlock } from "@/components/analysis/performance-block";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  CATEGORIES,
  type Finding,
  type FindingFilters,
  NO_FILTERS,
  SEVERITIES,
  filesOf,
  filterFindings,
  fixableOf,
  toggled,
} from "@/lib/analysis/view";
import { analysisQuery } from "@/lib/api/queries";
import { ru } from "@/lib/i18n/ru";
import { cn } from "@/lib/utils";
import { type RightTab, useWorkspaceStore } from "@/stores/workspace-store";

const t = ru.analysis;

function FilterChip({
  label,
  count,
  pressed,
  className,
  onClick,
}: {
  label: string;
  count: number;
  pressed: boolean;
  className?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      className={cn(
        "rounded-full border px-2 py-0.5 text-[10px] transition-opacity",
        className,
        !pressed && "opacity-40",
      )}
      onClick={onClick}
    >
      {label} ({count})
    </button>
  );
}

export function AnalysisPanel({ projectId }: { projectId: number }) {
  const analysis = useQuery(analysisQuery(projectId));
  const rightTab = useWorkspaceStore((s) => s.rightTab);
  const setRightTab = useWorkspaceStore((s) => s.setRightTab);
  const [filters, setFilters] = useState<FindingFilters>(NO_FILTERS);
  const [fixTarget, setFixTarget] = useState<Finding | null>(null);

  const all = useMemo(() => analysis.data?.findings ?? [], [analysis.data]);
  const files = useMemo(() => filesOf(all), [all]);
  const findings = useMemo(() => filterFindings(all, filters), [all, filters]);
  const fixable = useMemo(() => fixableOf(findings), [findings]);
  const data = analysis.data;

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label={t.title}>
      <div className="flex items-center border-b px-3 py-2">
        <h2 className="text-sm font-semibold">{t.title}</h2>
      </div>

      {analysis.isLoading && <Skeleton className="m-3 h-40" />}
      {analysis.error && (
        <p className="p-3 text-xs text-destructive">
          {t.loadFailed}: {analysis.error.message}
        </p>
      )}

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
            <div className="space-y-1.5 px-2 pt-2" data-testid="finding-filters">
              <div className="flex flex-wrap items-center gap-1" role="group" aria-label={t.filters.severity}>
                {SEVERITIES.map((severity) => (
                  <FilterChip
                    key={severity}
                    label={t.severity[severity]}
                    count={all.filter((f) => f.severity === severity).length}
                    pressed={filters.severities.has(severity)}
                    className={SEVERITY_STYLE[severity]}
                    onClick={() =>
                      setFilters((f) => ({ ...f, severities: toggled(f.severities, severity) }))
                    }
                  />
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-1" role="group" aria-label={t.filters.category}>
                {CATEGORIES.map((category) => (
                  <FilterChip
                    key={category}
                    label={t.categories[category]}
                    count={all.filter((f) => f.category === category).length}
                    pressed={filters.categories.has(category)}
                    onClick={() =>
                      setFilters((f) => ({ ...f, categories: toggled(f.categories, category) }))
                    }
                  />
                ))}
                {files.length > 1 && (
                  <select
                    aria-label={t.filters.file}
                    className="ml-auto rounded border bg-background px-1.5 py-0.5 text-[10px]"
                    value={filters.file ?? ""}
                    onChange={(event) =>
                      setFilters((f) => ({ ...f, file: event.target.value || null }))
                    }
                  >
                    <option value="">{t.filters.allFiles}</option>
                    {files.map((file) => (
                      <option key={file} value={file}>
                        {file}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            </div>
          )}

          <ScrollArea className="min-h-0 flex-1">
            <TabsContent value="problems" className="p-2">
              {findings.length === 0 && <p className="p-2 text-sm text-muted-foreground">{t.empty}</p>}
              <ul className="space-y-2">
                {findings.map((finding) => (
                  <FindingCard
                    key={finding.id}
                    projectId={projectId}
                    finding={finding}
                    onFix={setFixTarget}
                  />
                ))}
              </ul>
            </TabsContent>
            <TabsContent value="recommendations" className="p-2">
              {fixable.length === 0 && <p className="p-2 text-sm text-muted-foreground">{t.empty}</p>}
              <ul className="space-y-2">
                {fixable.map((finding) => (
                  <FindingCard
                    key={finding.id}
                    projectId={projectId}
                    finding={finding}
                    withFix
                    onFix={setFixTarget}
                  />
                ))}
              </ul>
            </TabsContent>
            <TabsContent value="metrics">
              <MetricsTab projectId={projectId} />
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
                  <span>{t.afterFix}</span>
                  <code className="rounded bg-node-return/15 px-1.5 py-0.5 text-node-return">
                    {data.complexity.optimized}
                  </code>
                </>
              )}
            </p>
            <PerformanceBlock projectId={projectId} />
          </div>
        </Tabs>
      )}

      <FixDialog projectId={projectId} finding={fixTarget} onClose={() => setFixTarget(null)} />
    </section>
  );
}
