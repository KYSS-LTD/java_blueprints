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
import { useState } from "react";

import { ImportDialog } from "@/components/projects/import-dialog";
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
import { projectQuery, versionsQuery } from "@/lib/api/queries";
import { ru } from "@/lib/i18n/ru";
import { cn } from "@/lib/utils";
import { isRunning } from "@/lib/run/run-state";
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

export function WorkspaceHeader({ projectId }: { projectId: number }) {
  const [importing, setImporting] = useState(false);
  const [running, setRunning] = useState(false);
  const project = useQuery(projectQuery(projectId));
  const versions = useQuery(versionsQuery(projectId));
  const busy = useRunStore((s) => {
    const id = s.order[s.order.length - 1];
    return id !== undefined && s.entries[id].kind === "run" && isRunning(s.entries[id].state);
  });

  const head = versions.data?.versions.find((v) => v.id === project.data?.head_version_id);

  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b bg-card px-3">
      <Link href="/" className="flex items-center gap-2 text-sm font-semibold" aria-label={ru.common.back}>
        <span className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">JB</span>
        <span className="hidden lg:inline">{ru.app.title}</span>
      </Link>
      <span className="text-muted-foreground">/</span>
      <h1 className="max-w-48 truncate text-sm font-medium" title={project.data?.name}>
        {project.data?.name ?? "…"}
      </h1>

      <div className="ml-2 flex items-center gap-1" aria-label={t.version}>
        <Badge variant="secondary" className="max-w-64 truncate font-mono text-[11px]" title={head?.description}>
          {head ? `v${head.number} · ${head.description || t.original}` : "v…"}
        </Badge>
        <SoonButton label={t.undo} hint={t.soonEdit} className="size-8 px-0">
          <Undo2 />
        </SoonButton>
        <SoonButton label={t.redo} hint={t.soonEdit} className="size-8 px-0">
          <Redo2 />
        </SoonButton>
        <SoonButton label={t.history} hint={t.soonEdit} className="size-8 px-0">
          <History />
        </SoonButton>
      </div>

      <div className="ml-auto flex items-center gap-2">
        <BehaviorBadge projectId={projectId} />

        <Button variant="outline" size="sm" onClick={() => setImporting(true)}>
          <Import /> {t.import}
        </Button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm">
              <Download /> {t.export}
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

        <SoonButton label={t.optimize} hint={t.soonOptimize}>
          <Sparkles /> {t.optimize}
        </SoonButton>

        <Button size="sm" onClick={() => setRunning(true)} disabled={busy} data-testid="run-button">
          <Play /> {t.run}
        </Button>

        <SoonButton label={t.debug} hint={t.soonDebug}>
          <Bug /> {t.debug}
        </SoonButton>

        <SoonButton label={t.settings} hint={ru.common.soon} className="size-8 px-0">
          <Settings />
        </SoonButton>
      </div>

      <ImportDialog open={importing} onOpenChange={setImporting} />
      <RunDialog projectId={projectId} open={running} onOpenChange={setRunning} />
    </header>
  );
}
