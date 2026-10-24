"use client";

import { ArrowRight, Boxes, Scissors, TriangleAlert, Wrench } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { PreconditionList } from "@/components/analysis/precondition-list";
import { VerifyBlock } from "@/components/analysis/verify-block";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Finding, Severity } from "@/lib/analysis/view";
import { ru } from "@/lib/i18n/ru";
import { cn } from "@/lib/utils";
import { useWorkspaceStore } from "@/stores/workspace-store";

const t = ru.analysis;

export const SEVERITY_STYLE: Record<Severity, string> = {
  high: "bg-severity-high/20 text-severity-high border-severity-high/40",
  medium: "bg-severity-medium/20 text-severity-medium border-severity-medium/40",
  low: "bg-severity-low/20 text-severity-low border-severity-low/40",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <Badge variant="outline" className={cn("shrink-0 text-[10px]", SEVERITY_STYLE[severity])}>
      {t.severity[severity]}
    </Badge>
  );
}

/** Выбор находки: открывает метод, показывает место в коде и выделяет узел на схеме. */
export function useOpenFinding() {
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

function ComplexityChange({ finding }: { finding: Finding }) {
  if (!finding.complexity?.after) return null;
  return (
    <p className="mt-2 flex items-center gap-1.5 text-xs">
      <code className="rounded bg-muted px-1.5 py-0.5">{finding.complexity.current}</code>
      <ArrowRight className="size-3 text-muted-foreground" aria-hidden />
      <code className="rounded bg-node-return/15 px-1.5 py-0.5 text-node-return">
        {finding.complexity.after}
      </code>
    </p>
  );
}

/** Рефакторинги без автоисправления: фрагмент или план выбирает пользователь (docs/06 § 5–6). */
const REFACTORINGS: Record<string, "extract" | "decompose"> = {
  "MAINT-01": "extract",
  "MAINT-08": "extract",
  "MAINT-05": "decompose",
};

function RefactorRow({ finding }: { finding: Finding }) {
  const kind = REFACTORINGS[finding.rule_id];
  const openFinding = useOpenFinding();
  const openDecompose = useWorkspaceStore((s) => s.openDecompose);
  if (!kind) return null;
  return (
    <div className="mt-2 flex items-center justify-between gap-2 border-t pt-2" data-testid="refactor-row">
      <span className="min-w-0 text-xs text-muted-foreground">
        <code>{kind === "extract" ? "REF-01" : "REF-04"}</code>
      </span>
      {kind === "extract" ? (
        <Button
          size="xs"
          variant="secondary"
          data-testid="refactor-extract"
          onClick={() => {
            openFinding(finding);
            toast(t.refactor.extractHint);
          }}
        >
          <Scissors /> {t.refactor.extract}
        </Button>
      ) : (
        <Button size="xs" variant="secondary" data-testid="refactor-decompose" onClick={openDecompose}>
          <Boxes /> {t.refactor.decompose}
        </Button>
      )}
    </div>
  );
}

function FixRow({
  finding,
  detailed,
  onFix,
}: {
  finding: Finding;
  /** Во вкладке «Рекомендации» предусловия видны сразу. */
  detailed: boolean;
  onFix: (finding: Finding) => void;
}) {
  const fix = finding.fix;
  if (!fix) return null;
  const failed = fix.preconditions.filter((p) => !p.passed);
  return (
    <div
      className="mt-2 space-y-1.5 border-t pt-2"
      data-testid="fix-row"
      data-available={fix.available}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 text-xs text-muted-foreground">
          <code>{fix.fix_id}</code>
          {fix.available && ` · ${fix.behavior_preserving ? t.fixSafe : t.fixChanges}`}
          {!fix.available && ` · ${t.fixUnavailable}`}
        </span>
        {fix.available && (
          <Button
            size="xs"
            variant="secondary"
            onClick={() => onFix(finding)}
            data-testid="fix-preview"
          >
            <Wrench /> {t.preview}
          </Button>
        )}
      </div>
      {/* исправление недоступно — видно, какое предусловие не выполнено; доступное — предусловия по запросу вкладки */}
      {!fix.available && <PreconditionList preconditions={failed} />}
      {fix.available && detailed && <PreconditionList preconditions={fix.preconditions} />}
    </div>
  );
}

export function FindingCard({
  projectId,
  finding,
  withFix,
  onFix,
}: {
  projectId: number;
  finding: Finding;
  /** Вкладка «Рекомендации»: исправление и предусловия на виду. */
  withFix?: boolean;
  onFix: (finding: Finding) => void;
}) {
  const [open, setOpen] = useState(false);
  const openFinding = useOpenFinding();
  const selected = useWorkspaceStore(
    (s) =>
      s.selection !== null &&
      s.selection.nodeId === (finding.location.node_id ?? finding.location.method_id),
  );
  return (
    <li
      className={cn(
        "rounded-lg border bg-card p-3 text-sm",
        selected && "border-primary ring-1 ring-primary",
      )}
      data-testid="finding-card"
      data-rule={finding.rule_id}
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

      <ComplexityChange finding={finding} />

      <Button
        variant="link"
        size="xs"
        className="mt-1 h-auto p-0 text-xs"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {open ? t.hide : t.why}
      </Button>
      {open && (
        <div className="mt-2 space-y-2 border-t pt-2 text-xs" data-testid="finding-details">
          <p>
            <b>{t.defect}:</b> {finding.defect}
          </p>
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
          {finding.evidence.static.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">
              {finding.evidence.static.map((e) => (
                <li key={e.kind + e.text}>{e.text}</li>
              ))}
            </ul>
          )}
          <VerifyBlock projectId={projectId} finding={finding} />
        </div>
      )}

      <FixRow finding={finding} detailed={withFix === true} onFix={onFix} />
      <RefactorRow finding={finding} />
    </li>
  );
}
