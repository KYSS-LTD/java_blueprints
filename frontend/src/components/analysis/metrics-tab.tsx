"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useMemo } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { formatMetric, hardestMethods, metricRows } from "@/lib/analysis/metrics";
import { metricsQuery, projectQuery } from "@/lib/api/queries";
import { ru } from "@/lib/i18n/ru";
import { cn } from "@/lib/utils";
import { useWorkspaceStore } from "@/stores/workspace-store";

const t = ru.analysis;

/** Вкладка «Метрики»: «Оригинал → Текущая» и самые сложные методы. */
export function MetricsTab({ projectId }: { projectId: number }) {
  const project = useQuery(projectQuery(projectId));
  const original = project.data?.original_version_id;
  const head = project.data?.head_version_id;
  // с оригиналом сравниваем, только если HEAD — другая версия
  const compareTo = original !== undefined && original !== head ? original : null;
  const metrics = useQuery({ ...metricsQuery(projectId, compareTo), enabled: project.isSuccess });
  const openMethod = useWorkspaceStore((s) => s.openMethod);

  const data = metrics.data;
  const rows = useMemo(() => (data ? metricRows(data.metrics, data.compare_to) : []), [data]);
  const hardest = useMemo(() => (data ? hardestMethods(data.metrics) : []), [data]);
  const coverage = project.data?.gate.coverage ?? null;
  const compared = data?.compare_to != null;

  if (metrics.isLoading) return <Skeleton className="m-3 h-40" />;
  if (metrics.error) {
    return (
      <p className="p-3 text-xs text-destructive">
        {t.loadFailed}: {metrics.error.message}
      </p>
    );
  }
  if (!data) return null;

  return (
    <div className="space-y-4 p-3" data-testid="metrics-tab">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-muted-foreground">
            <th className="pb-1 font-normal" />
            {compared && (
              <th className="pb-1 text-right font-normal">{t.metricsCompare.original}</th>
            )}
            <th className="pb-1 text-right font-normal">{t.metricsCompare.current}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} data-testid={`metric-${row.key}`}>
              <td className="py-0.5 text-muted-foreground">{t.metricLabels[row.key]}</td>
              {compared && (
                <td className="py-0.5 text-right font-mono text-muted-foreground">
                  {row.before === null ? "—" : formatMetric(row.before)}
                </td>
              )}
              <td
                className={cn(
                  "py-0.5 text-right font-mono",
                  row.improved === true && "text-node-return",
                  row.improved === false && "text-severity-high",
                )}
              >
                {compared && row.before !== null && row.before !== row.after && (
                  <ArrowRight className="mr-1 inline size-3 text-muted-foreground" aria-hidden />
                )}
                {formatMetric(row.after)}
              </td>
            </tr>
          ))}
          <tr data-testid="metric-coverage">
            <td className="py-0.5 text-muted-foreground">{t.metricLabels.coverage}</td>
            {compared && <td />}
            <td className="py-0.5 text-right font-mono">
              {coverage === null ? "—" : `${Math.round(coverage * 100)}%`}
            </td>
          </tr>
        </tbody>
      </table>

      <section aria-label={t.metricsTable.title}>
        <h3 className="mb-1 text-xs font-semibold text-muted-foreground">{t.metricsTable.title}</h3>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th className="pb-1 font-normal">{t.metricsTable.method}</th>
              <th className="pb-1 text-right font-normal">{t.metricsTable.lines}</th>
              <th className="pb-1 text-right font-normal">{t.metricsTable.cyclomatic}</th>
              <th className="pb-1 text-right font-normal">{t.metricsTable.complexity}</th>
            </tr>
          </thead>
          <tbody>
            {hardest.map((method) => (
              <tr key={method.method_id} className="hover:bg-accent">
                <td className="max-w-40 truncate py-0.5">
                  <button
                    type="button"
                    className="truncate text-left font-mono"
                    title={method.method_id}
                    onClick={() => openMethod(method.method_id)}
                  >
                    {method.name}
                  </button>
                </td>
                <td className="py-0.5 text-right font-mono">{method.lines}</td>
                <td className="py-0.5 text-right font-mono">{method.cyclomatic}</td>
                <td className="py-0.5 text-right font-mono">{method.complexity ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
