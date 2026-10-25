"use client";

import { useQuery } from "@tanstack/react-query";
import { CircleAlert } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef } from "react";
import { useDefaultLayout } from "react-resizable-panels";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AnalysisPanel } from "@/components/workspace/analysis-panel";
import { BlockPalette } from "@/components/workspace/block-palette";
import { CenterArea } from "@/components/workspace/center-area";
import { CodePanel } from "@/components/workspace/code-panel";
import { ConsolePanel } from "@/components/workspace/console-panel";
import { ExtractDialog } from "@/components/workspace/extract-dialog";
import { ProjectTree } from "@/components/workspace/project-tree";
import { TestsPanel } from "@/components/workspace/tests-panel";
import { WorkspaceHeader } from "@/components/workspace/workspace-header";
import { projectQuery } from "@/lib/api/queries";
import { useHistoryHotkeys } from "@/lib/edit/use-hotkeys";
import { useEditing } from "@/lib/edit/use-editing";
import { useIsClient } from "@/lib/hooks/use-is-client";
import { useElementWidth } from "@/lib/hooks/use-element-width";
import { ru } from "@/lib/i18n/ru";
import { useEditorStore } from "@/stores/editor-store";
import { useRunStore } from "@/stores/run-store";
import {
  type BottomTab,
  formatWorkspaceLocation,
  parseWorkspaceLocation,
  useWorkspaceStore,
} from "@/stores/workspace-store";

const t = ru.workspace;

/** Размеры панелей сохраняются в localStorage (docs/09 § 1). */
function usePanelLayout(id: string) {
  return useDefaultLayout({
    id: `jb:${id}`,
    storage: typeof window === "undefined" ? undefined : window.localStorage,
  });
}

/** `?file&method&node`: читается один раз при открытии, дальше состояние пишется в адрес (docs/09 § 2). */
function useLocationSync(ready: boolean) {
  useEffect(() => {
    if (!ready) return;
    const initial = parseWorkspaceLocation(window.location.search);
    const store = useWorkspaceStore.getState();
    if (initial.method) store.openMethod(initial.method);
    if (initial.file) store.setActiveFile(initial.file);
    if (initial.node) store.selectNode(initial.node, "url");

    return useWorkspaceStore.subscribe((state) => {
      const active = state.tabs.find((tab) => tab.id === state.activeTabId);
      const search = formatWorkspaceLocation({
        file: state.activeFile,
        method: active?.kind === "method" ? active.methodId : null,
        node: state.selection?.nodeId ?? null,
      });
      if (search !== window.location.search) {
        window.history.replaceState(null, "", `${window.location.pathname}${search}`);
      }
    });
  }, [ready]);
}

/** Ширина области, с которой «Код Java», «Консоль» и «Результат тестов» стоят рядом, как в макете; уже — вкладки. */
export const SIDE_BY_SIDE_MIN_WIDTH = 640;

function BottomPanels({ projectId, versionId }: { projectId: number; versionId?: number }) {
  const area = useRef<HTMLDivElement>(null);
  const width = useElementWidth(area);
  const bottomTab = useWorkspaceStore((s) => s.bottomTab);
  const setBottomTab = useWorkspaceStore((s) => s.setBottomTab);
  const layout = usePanelLayout("bottom");

  return (
    <div ref={area} className="h-full min-h-0">
      {width !== null && width >= SIDE_BY_SIDE_MIN_WIDTH && (
        <ResizablePanelGroup
          orientation="horizontal"
          defaultLayout={layout.defaultLayout}
          onLayoutChanged={layout.onLayoutChanged}
        >
          <ResizablePanel id="code" defaultSize="38%" minSize="20%">
            <CodePanel projectId={projectId} versionId={versionId} />
          </ResizablePanel>
          <ResizableHandle />
          <ResizablePanel id="console" defaultSize="24%" minSize="15%">
            <ConsolePanel />
          </ResizablePanel>
          <ResizableHandle />
          <ResizablePanel id="tests" defaultSize="38%" minSize="15%">
            <TestsPanel projectId={projectId} />
          </ResizablePanel>
        </ResizablePanelGroup>
      )}
      {width !== null && width < SIDE_BY_SIDE_MIN_WIDTH && (
        <Tabs
          value={bottomTab}
          onValueChange={(value) => setBottomTab(value as BottomTab)}
          className="h-full min-h-0 gap-0"
        >
          <TabsList className="mx-2 mt-1.5 h-8 w-auto self-start">
            <TabsTrigger value="code" className="text-xs">
              {ru.code.title}
            </TabsTrigger>
            <TabsTrigger value="console" className="text-xs">
              {ru.console.title}
            </TabsTrigger>
            <TabsTrigger value="tests" className="text-xs">
              {ru.tests.title}
            </TabsTrigger>
          </TabsList>
          <TabsContent value="code" className="min-h-0">
            <CodePanel projectId={projectId} versionId={versionId} />
          </TabsContent>
          <TabsContent value="console" className="min-h-0">
            <ConsolePanel />
          </TabsContent>
          <TabsContent value="tests" className="min-h-0">
            <TestsPanel projectId={projectId} />
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}

function Layout({ projectId, versionId }: { projectId: number; versionId?: number }) {
  const main = usePanelLayout("main");
  const left = usePanelLayout("left");
  const centre = usePanelLayout("centre");

  return (
    <ResizablePanelGroup
      orientation="horizontal"
      className="min-h-0 flex-1"
      defaultLayout={main.defaultLayout}
      onLayoutChanged={main.onLayoutChanged}
    >
      <ResizablePanel id="left" defaultSize="19%" minSize="12%" maxSize="32%">
        <ResizablePanelGroup
          orientation="vertical"
          defaultLayout={left.defaultLayout}
          onLayoutChanged={left.onLayoutChanged}
        >
          <ResizablePanel id="tree" defaultSize="64%" minSize="25%">
            <ProjectTree projectId={projectId} versionId={versionId} />
          </ResizablePanel>
          <ResizableHandle />
          <ResizablePanel id="palette" defaultSize="36%" minSize="15%">
            <BlockPalette projectId={projectId} />
          </ResizablePanel>
        </ResizablePanelGroup>
      </ResizablePanel>
      <ResizableHandle />
      <ResizablePanel id="centre" defaultSize="52%" minSize="30%">
        <ResizablePanelGroup
          orientation="vertical"
          defaultLayout={centre.defaultLayout}
          onLayoutChanged={centre.onLayoutChanged}
        >
          <ResizablePanel id="canvas" defaultSize="62%" minSize="25%">
            <CenterArea projectId={projectId} versionId={versionId} />
          </ResizablePanel>
          <ResizableHandle />
          <ResizablePanel id="bottom" defaultSize="38%" minSize="15%">
            <BottomPanels projectId={projectId} versionId={versionId} />
          </ResizablePanel>
        </ResizablePanelGroup>
      </ResizablePanel>
      <ResizableHandle />
      <ResizablePanel id="right" defaultSize="29%" minSize="18%" maxSize="42%">
        <AnalysisPanel projectId={projectId} />
      </ResizablePanel>
    </ResizablePanelGroup>
  );
}

/** Горячие клавиши отмены и повтора версий: работают, пока фокус не в поле ввода или редакторе. */
function HistoryHotkeys({ projectId }: { projectId: number }) {
  const { canUndo, canRedo, working, undo, redo } = useEditing(projectId);
  useHistoryHotkeys({
    onUndo: () => canUndo && !working && void undo(),
    onRedo: () => canRedo && !working && void redo(),
  });
  return null;
}

export function Workspace({ projectId }: { projectId: number }) {
  const project = useQuery(projectQuery(projectId));
  const ready = useIsClient() && project.isSuccess;

  // Состояние рабочей области принадлежит проекту: при смене проекта начинаем с чистого листа.
  useEffect(() => {
    useWorkspaceStore.getState().reset();
    useEditorStore.getState().reset();
    useRunStore.getState().clear();
    return () => {
      useRunStore.getState().clear();
      useEditorStore.getState().reset();
    };
  }, [projectId]);

  useLocationSync(ready);

  if (project.error) {
    return (
      <main className="mx-auto flex w-full max-w-xl flex-1 flex-col justify-center gap-4 p-6">
        <Alert variant="destructive">
          <CircleAlert />
          <AlertTitle>{t.loadFailed}</AlertTitle>
          <AlertDescription>{project.error.message}</AlertDescription>
        </Alert>
        <Button asChild variant="outline" className="self-start">
          <Link href="/">{ru.common.back}</Link>
        </Button>
      </main>
    );
  }

  return (
    <div className="flex h-screen min-h-0 flex-col" data-testid="workspace">
      {ready && <HistoryHotkeys projectId={projectId} />}
      {ready && <ExtractDialog projectId={projectId} />}
      <WorkspaceHeader projectId={projectId} />
      {ready ? (
        <Layout projectId={projectId} versionId={project.data?.head_version_id} />
      ) : (
        <div className="grid flex-1 grid-cols-[19%_1fr_29%] gap-px p-px">
          <Skeleton className="rounded-none" />
          <Skeleton className="rounded-none" />
          <Skeleton className="rounded-none" />
        </div>
      )}
    </div>
  );
}
