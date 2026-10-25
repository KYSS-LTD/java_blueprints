"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Bug,
  CircleCheck,
  CircleHelp,
  CircleX,
  Download,
  History,
  Import,
  Play,
  Redo2,
  Settings,
  Sparkles,
  Undo2,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { OptimizeDialog } from "@/components/analysis/optimize-dialog";
import { ImportDialog } from "@/components/projects/import-dialog";
import { ThemeToggle } from "@/components/theme-toggle";
import { HistorySheet } from "@/components/workspace/history-sheet";
import { RunDialog } from "@/components/workspace/run-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { exportUrl } from "@/lib/api/projects";
import { projectQuery } from "@/lib/api/queries";
import { useEditing } from "@/lib/edit/use-editing";
import { ru } from "@/lib/i18n/ru";
import { cn } from "@/lib/utils";
import { isRunning } from "@/lib/run/run-state";
import { useDebugStore } from "@/stores/debug-store";
import { useEditorStore } from "@/stores/editor-store";
import { useRunStore } from "@/stores/run-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

const t = ru.header;

function SoonButton({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {/* span: у отключённой кнопки нет событий мыши, а подсказка нужна */}
        <span>
          <Button variant="outline" size="sm" disabled className={className} aria-label={`${label}. ${hint}`}>
            {children}
          </Button>
        </span>
      </TooltipTrigger>
      <TooltipContent>{hint}</TooltipContent>
    </Tooltip>
  );
}

function IconButton({
  label,
  disabled,
  onClick,
  children,
  testId,
}: {
  label: string;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {/* span: у отключённой кнопки нет событий мыши, а подсказка нужна */}
        <span>
          <Button
            variant="outline"
            size="sm"
            className="size-8 px-0"
            disabled={disabled}
            onClick={onClick}
            aria-label={label}
            data-testid={testId}
          >
            {children}
          </Button>
        </span>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

/** Состояние автосохранения кода рядом с версией. */
function SaveIndicator() {
  const state = useEditorStore((s) => s.saveState);
  const error = useEditorStore((s) => s.saveError);
  if (state === "idle") return null;
  const label =
    state === "saving" ? t.saving : state === "error" ? `${t.saveFailed}${error ? `: ${error}` : ""}` : t.unsaved;
  return (
    <span
      className={cn("text-[11px]", state === "error" ? "text-destructive" : "text-muted-foreground")}
      role="status"
      data-testid="save-state"
      title={error ?? undefined}
    >
      {label}
    </span>
  );
}

function BehaviorBadge({ projectId }: { projectId: number }) {
  const project = useQuery(projectQuery(projectId));
  const setBottomTab = useWorkspaceStore((s) => s.setBottomTab);
  const gate = project.data?.gate;
  if (!gate) return null;
  const counts = gate.total ? ` (${gate.passed ?? 0}/${gate.total})` : "";
  const view =
    gate.status === "passed"
      ? { icon: CircleCheck, label: `✓ ${t.behavior.passed}${counts}`, className: "border-node-return/50 text-node-return" }
      : gate.status === "failed"
        ? { icon: CircleX, label: `✗ ${t.behavior.failed}${counts}`, className: "border-severity-high/50 text-severity-high" }
        : { icon: CircleHelp, label: `? ${t.behavior.unknown}`, className: "text-muted-foreground" };
  return (
    <button type="button" onClick={() => setBottomTab("tests")} data-testid="behavior-badge">
      <Badge variant="outline" className={cn("cursor-pointer gap-1.5", view.className)}>
        <view.icon className="size-3.5" aria-hidden />
        {view.label}
      </Badge>
    </button>
  );
}

/** Подписи второстепенных кнопок шапки показываются, когда хватает ширины; иначе остаются значки. */
const LABEL = "hidden min-[1366px]:inline";

export function WorkspaceHeader({ projectId }: { projectId: number }) {
  const [importing, setImporting] = useState(false);
  const [running, setRunning] = useState(false);
  const [debugging, setDebugging] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const project = useQuery(projectQuery(projectId));
  const { head, canUndo, canRedo, working, undo, redo } = useEditing(projectId);
  const busy = useRunStore((s) => {
    const id = s.order[s.order.length - 1];
    return id !== undefined && s.entries[id].kind === "run" && isRunning(s.entries[id].state);
  });
  const tracing = useRunStore((s) => {
    const id = s.order[s.order.length - 1];
    return id !== undefined && s.entries[id].kind === "trace" && isRunning(s.entries[id].state);
  });
  const trace = useDebugStore((s) => s.trace);
  const clearTrace = useDebugStore((s) => s.clear);
  // счётчики относятся к версии, на которой шла трассировка: после правки идентификаторы блоков уже другие
  useEffect(() => {
    if (trace && head && trace.versionId !== null && trace.versionId !== head.id) clearTrace();
  }, [trace, head, clearTrace]);

  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b bg-card px-3">
      <Link href="/" className="flex items-center gap-2 text-sm font-semibold">
        <span className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
          JB
        </span>
        <span className="hidden whitespace-nowrap 2xl:inline">{ru.app.title}</span>
        <span className="sr-only">: {ru.common.back}</span>
      </Link>
      <span className="text-muted-foreground">/</span>
      <h1 className="max-w-48 truncate text-sm font-medium" title={project.data?.name}>
        {project.data?.name ?? "…"}
      </h1>

      <div className="ml-2 flex items-center gap-1" aria-label={t.version}>
        <Badge variant="secondary" className="max-w-64 truncate font-mono text-[11px]" title={head?.description}>
          {head ? `v${head.number} · ${head.description || t.original}` : "v…"}
        </Badge>
        <IconButton label={`${t.undo} (Ctrl+Z)`} disabled={!canUndo || working} onClick={() => void undo()} testId="undo-button">
          <Undo2 />
        </IconButton>
        <IconButton label={`${t.redo} (Ctrl+Shift+Z)`} disabled={!canRedo || working} onClick={() => void redo()} testId="redo-button">
          <Redo2 />
        </IconButton>
        <IconButton label={t.history} onClick={() => setHistoryOpen(true)} testId="history-button">
          <History />
        </IconButton>
        <SaveIndicator />
      </div>

      <div className="ml-auto flex items-center gap-2">
        <BehaviorBadge projectId={projectId} />

        <Button variant="outline" size="sm" onClick={() => setImporting(true)} aria-label={t.import} title={t.import}>
          <Import /> <span className={LABEL}>{t.import}</span>
        </Button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" aria-label={t.export} title={t.export}>
              <Download /> <span className={LABEL}>{t.export}</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem asChild>
              <a href={exportUrl(projectId, "source")} download>
                {t.exportSource}
              </a>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <a href={exportUrl(projectId, "report")} download>
                {t.exportReport}
              </a>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <a href={exportUrl(projectId, "bundle")} download>
                {t.exportBundle}
              </a>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        <Button
          variant="outline"
          size="sm"
          onClick={() => setOptimizing(true)}
          title={t.optimizeHint}
          data-testid="optimize-button"
        >
          <Sparkles /> {t.optimize}
        </Button>

        <Button size="sm" onClick={() => setRunning(true)} disabled={busy} data-testid="run-button">
          <Play /> {t.run}
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={() => setDebugging(true)}
          disabled={tracing}
          title={t.debugHint}
          aria-label={t.debug}
          data-testid="debug-button"
        >
          <Bug /> <span className={LABEL}>{t.debug}</span>
        </Button>

        <SoonButton label={t.settings} hint={ru.common.soon} className="size-8 px-0">
          <Settings />
        </SoonButton>
        <ThemeToggle />
      </div>

      <ImportDialog open={importing} onOpenChange={setImporting} />
      <RunDialog projectId={projectId} open={running} onOpenChange={setRunning} />
      <RunDialog projectId={projectId} open={debugging} onOpenChange={setDebugging} mode="trace" />
      <HistorySheet projectId={projectId} open={historyOpen} onOpenChange={setHistoryOpen} />
      <OptimizeDialog projectId={projectId} open={optimizing} onOpenChange={setOptimizing} />
    </header>
  );
}
