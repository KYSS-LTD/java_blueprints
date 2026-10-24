"use client";

import { Badge } from "@/components/ui/badge";
import { ru } from "@/lib/i18n/ru";
import type { RunState } from "@/lib/run/run-state";
import { cn } from "@/lib/utils";

const t = ru.tests;

/** Стадии проверки версии (синтаксис → компиляция → сценарии → покрытие): последнее событие по каждой. */
export function GateStages({ gate, className }: { gate: RunState["gate"]; className?: string }) {
  const stages = new Map(gate.map((g) => [g.stage, g]));
  if (stages.size === 0) return null;
  return (
    <ol className={cn("flex flex-wrap gap-1 text-[10px]", className)} aria-label={t.started.gate}>
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
