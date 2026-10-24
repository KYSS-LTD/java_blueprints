"use client";

import { useQuery } from "@tanstack/react-query";
import { CircleAlert, Network, SquareFunction, X } from "lucide-react";
import { useCallback } from "react";

import { FlowCanvas } from "@/components/graph/flow-canvas";
import { StructureCanvas } from "@/components/graph/structure-canvas";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { NodeInspector } from "@/components/workspace/node-inspector";
import { ApiError, fileQuery, flowQuery, structureQuery } from "@/lib/api/queries";
import { modelOf } from "@/lib/graph/flow-cache";
import { ru } from "@/lib/i18n/ru";
import { cn } from "@/lib/utils";
import { useEditorStore } from "@/stores/editor-store";
import { type CenterTab, useWorkspaceStore } from "@/stores/workspace-store";

const t = ru.workspace;

type Props = { projectId: number; versionId?: number };

function TabBar() {
  const tabs = useWorkspaceStore((s) => s.tabs);
  const activeTabId = useWorkspaceStore((s) => s.activeTabId);
  const setActiveTab = useWorkspaceStore((s) => s.setActiveTab);
  const closeTab = useWorkspaceStore((s) => s.closeTab);
  const openStructure = useWorkspaceStore((s) => s.openStructure);
  const hasStructure = tabs.some((tab) => tab.kind === "structure");

  return (
    <div className="flex h-9 shrink-0 items-end gap-0.5 overflow-x-auto border-b bg-card/60 px-2" role="tablist">
      {tabs.map((tab) => (
        <TabButton
          key={tab.id}
          tab={tab}
          active={tab.id === activeTabId}
          onSelect={() => setActiveTab(tab.id)}
          onClose={() => closeTab(tab.id)}
        />
      ))}
      {!hasStructure && (
        <Button
          variant="ghost"
          size="xs"
          className="mb-1 ml-1 text-muted-foreground"
          onClick={openStructure}
          aria-label={ru.tabs.structure}
        >
          <Network /> {ru.tabs.structure}
        </Button>
      )}
    </div>
  );
}

function TabButton({
  tab,
  active,
  onSelect,
  onClose,
}: {
  tab: CenterTab;
  active: boolean;
  onSelect: () => void;
  onClose: () => void;
}) {
  const Icon = tab.kind === "structure" ? Network : SquareFunction;
  return (
    <div
      role="tab"
      aria-selected={active}
      tabIndex={0}
      className={cn(
        "group flex max-w-56 shrink-0 cursor-pointer items-center gap-1.5 rounded-t-md border border-b-0 px-2.5 py-1.5 text-xs outline-none focus-visible:ring-1 focus-visible:ring-ring",
        active ? "bg-background text-foreground" : "border-transparent text-muted-foreground hover:bg-accent/50",
      )}
      onClick={onSelect}
      onAuxClick={(event) => event.button === 1 && onClose()}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") onSelect();
        if (event.key === "Delete") onClose();
      }}
      title={tab.kind === "method" ? tab.methodId : tab.title}
    >
      <Icon className="size-3.5 shrink-0" aria-hidden />
      <span className="truncate">{tab.title}</span>
      <button
        type="button"
        tabIndex={-1}
        aria-label={`${ru.tabs.close}: ${tab.title}`}
        className="rounded p-0.5 opacity-60 hover:bg-accent hover:opacity-100"
        onClick={(event) => {
          event.stopPropagation();
          onClose();
        }}
      >
        <X className="size-3" />
      </button>
    </div>
  );
}

function ErrorBox({ title, message }: { title: string; message: string }) {
  return (
    <div className="p-4">
      <Alert variant="destructive">
        <CircleAlert />
        <AlertTitle>{title}</AlertTitle>
        <AlertDescription>{message}</AlertDescription>
      </Alert>
    </div>
  );
}

function MethodView({ projectId, versionId, methodId }: Props & { methodId: string }) {
  const openMethod = useWorkspaceStore((s) => s.openMethod);
  const flow = useQuery(flowQuery(projectId, methodId, versionId));
  // Пока код набирается, схема берётся из живого разбора; без правок это обычная схема версии.
  const live = useEditorStore((s) => s.liveFlows[methodId]);
  const shown = live ?? flow.data;
  const file = useQuery(fileQuery(projectId, shown?.file ?? null, versionId));
  const liveIssues = useEditorStore((s) => (shown ? s.issues[shown.file] : undefined));
  const issues = liveIssues ?? file.data?.syntax_errors ?? [];
  const syntaxError = issues[0];
  const onOpenMethod = useCallback((id: string) => openMethod(id), [openMethod]);

  if (!shown && flow.isLoading) return <Skeleton className="m-4 h-64" />;
  if (!shown && flow.error) {
    const gone = flow.error instanceof ApiError && flow.error.code === "NOT_FOUND";
    return <ErrorBox title={ru.graph.loadFailed} message={gone ? ru.graph.methodGone : flow.error.message} />;
  }
  if (!shown) return null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {syntaxError && (
        <div className="shrink-0 border-b border-destructive/40 bg-destructive/10 px-3 py-1.5 text-xs text-destructive" role="alert">
          {ru.graph.syntaxBanner} {syntaxError.range.start_line}: {syntaxError.message}. {ru.graph.syntaxBlocked}
        </div>
      )}
      <div className="relative min-h-0 flex-1">
        <FlowCanvas
          key={methodId}
          projectId={projectId}
          versionId={versionId}
          flow={shown}
          editable={syntaxError === undefined && versionId !== undefined}
          onOpenMethod={onOpenMethod}
        />
        {versionId !== undefined && (
          <NodeInspector
            projectId={projectId}
            versionId={versionId}
            methodId={methodId}
            model={modelOf(shown)}
          />
        )}
      </div>
    </div>
  );
}

function StructureView({ projectId, versionId }: Props) {
  const view = useWorkspaceStore((s) => s.structureMode);
  const setView = useWorkspaceStore((s) => s.setStructureMode);
  const openMethod = useWorkspaceStore((s) => s.openMethod);
  // Режим «methods» содержит и типы, и члены: представление «Типы» получается фильтром без второго запроса.
  const structure = useQuery(structureQuery(projectId, "methods", versionId));

  if (structure.isLoading) return <Skeleton className="m-4 h-64" />;
  if (structure.error) return <ErrorBox title={ru.graph.loadFailed} message={structure.error.message} />;
  if (!structure.data) return null;
  return (
    <StructureCanvas
      graph={structure.data}
      view={view}
      onViewChange={setView}
      onOpenMethod={openMethod}
    />
  );
}

export function CenterArea({ projectId, versionId }: Props) {
  const tabs = useWorkspaceStore((s) => s.tabs);
  const activeTabId = useWorkspaceStore((s) => s.activeTabId);
  const active = tabs.find((tab) => tab.id === activeTabId);

  return (
    <section className="flex h-full min-h-0 flex-col bg-background" aria-label={ru.graph.empty}>
      <TabBar />
      <div className="min-h-0 flex-1" role="tabpanel">
        {!active && (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
            <SquareFunction className="size-10 text-muted-foreground" aria-hidden />
            <p className="font-medium">{t.emptyCenter}</p>
            <p className="text-sm text-muted-foreground">{t.emptyCenterHint}</p>
          </div>
        )}
        {active?.kind === "method" && (
          <MethodView projectId={projectId} versionId={versionId} methodId={active.methodId} />
        )}
        {active?.kind === "structure" && <StructureView projectId={projectId} versionId={versionId} />}
      </div>
    </section>
  );
}
