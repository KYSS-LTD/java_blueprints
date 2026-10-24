import { Check, X } from "lucide-react";

import type { components } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

type Precondition = components["schemas"]["Precondition"];

/** Предусловия исправления: ✓ выполнено, ✗ нет (с причиной). */
export function PreconditionList({ preconditions }: { preconditions: Precondition[] }) {
  if (preconditions.length === 0) return null;
  return (
    <ul className="space-y-0.5 text-xs" data-testid="preconditions">
      {preconditions.map((p) => (
        <li key={p.name} className="flex items-start gap-1.5" data-passed={p.passed}>
          {p.passed ? (
            <Check className="mt-0.5 size-3 shrink-0 text-node-return" aria-label="выполнено" />
          ) : (
            <X className="mt-0.5 size-3 shrink-0 text-severity-high" aria-label="не выполнено" />
          )}
          <span className={cn(!p.passed && "text-severity-high")}>
            {p.name}
            {p.reason && p.reason !== p.name && (
              <span className="text-muted-foreground"> — {p.reason}</span>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}
