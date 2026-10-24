"use client";

import { useQuery } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { projectQuery, scenariosQuery } from "@/lib/api/queries";
import { ru } from "@/lib/i18n/ru";

const t = ru.tests;

export function TestsPanel({ projectId }: { projectId: number }) {
  const project = useQuery(projectQuery(projectId));
  const scenarios = useQuery(scenariosQuery(projectId));
  const gate = project.data?.gate;
  const list = scenarios.data?.scenarios ?? [];
  const passed = gate?.passed ?? 0;
  const total = gate?.total ?? 0;

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label={t.title}>
      <div className="flex flex-wrap items-center gap-2 border-b px-3 py-1.5">
        <h2 className="text-sm font-semibold">{t.title}</h2>
        {gate && gate.status !== "unknown" && total > 0 && (
          <span className="text-xs text-muted-foreground">
            {passed} из {total}
          </span>
        )}
        <div className="ml-auto flex gap-1">
          {[t.runScenarios, t.baseline].map((label) => (
            <Button key={label} variant="outline" size="xs" disabled title={t.empty}>
              {label}
            </Button>
          ))}
        </div>
      </div>

      {gate && gate.status !== "unknown" && total > 0 && (
        <div className="px-3 pt-2">
          <Progress value={(passed / total) * 100} aria-label={`${passed} из ${total}`} />
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
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.map((scenario) => (
                <TableRow key={scenario.id}>
                  <TableCell className="text-xs font-medium">{scenario.name}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className="text-[10px]">
                      {scenario.baseline.state === "none" ? "нет эталона" : scenario.baseline.state}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs">
                    {scenario.baseline.duration_ms === null ? "—" : `${scenario.baseline.duration_ms} мс`}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </section>
  );
}
