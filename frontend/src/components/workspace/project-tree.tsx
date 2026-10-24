"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Box,
  ChevronDown,
  ChevronRight,
  CircleDot,
  FileCode2,
  Folder,
  Hash,
  Search,
  SquareFunction,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { treeQuery } from "@/lib/api/queries";
import { defaultExpanded, pathTo, type TreeNode, type TreeRow, visibleRows } from "@/lib/graph/tree-model";
import { ru } from "@/lib/i18n/ru";
import { cn } from "@/lib/utils";
import { useWorkspaceStore } from "@/stores/workspace-store";

const t = ru.tree;

function NodeIcon({ node }: { node: TreeNode }) {
  const className = "size-3.5 shrink-0";
  if (node.kind === "package") return <Folder className={className} aria-hidden />;
  if (node.kind === "file") return <FileCode2 className={cn(className, "text-node-variable")} aria-hidden />;
  switch (node.member_kind) {
    case "class":
    case "interface":
    case "record":
    case "enum":
      return <Box className={cn(className, "text-node-method")} aria-hidden />;
    case "field":
      return <Hash className={cn(className, "text-muted-foreground")} aria-hidden />;
    case "method":
    case "constructor":
      return <SquareFunction className={cn(className, "text-node-operation")} aria-hidden />;
    default:
      return <CircleDot className={cn(className, "text-muted-foreground")} aria-hidden />;
  }
}

function FindingDots({ node }: { node: TreeNode }) {
  const { high, medium, low } = node.findings;
  if (high + medium + low === 0) return null;
  const dot = high > 0 ? "bg-severity-high" : medium > 0 ? "bg-severity-medium" : "bg-severity-low";
  return (
    <span
      className="flex shrink-0 items-center gap-1 text-[10px] text-muted-foreground"
      title={`${t.findings}: ${high + medium + low}`}
    >
      <span className={cn("size-2 rounded-full", dot)} aria-hidden />
      {high + medium + low}
    </span>
  );
}

type Props = { projectId: number; versionId?: number };

export function ProjectTree({ projectId, versionId }: Props) {
  const tree = useQuery(treeQuery(projectId, versionId));
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string> | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const activeTabId = useWorkspaceStore((s) => s.activeTabId);
  const selectedId = useWorkspaceStore((s) => s.selection?.nodeId ?? null);
  const openMethod = useWorkspaceStore((s) => s.openMethod);
  const openStructure = useWorkspaceStore((s) => s.openStructure);
  const selectNode = useWorkspaceStore((s) => s.selectNode);
  const setActiveFile = useWorkspaceStore((s) => s.setActiveFile);

  const roots = useMemo(() => tree.data?.roots ?? [], [tree.data]);
  const open = useMemo(() => expanded ?? defaultExpanded(roots), [expanded, roots]);
  const rows = useMemo(() => visibleRows(roots, open, search), [roots, open, search]);

  // Активная вкладка метода раскрывает путь к себе в дереве: подстраиваем состояние при смене вкладки,
  // а не в эффекте (https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes).
  const [revealedFor, setRevealedFor] = useState<string | null>(null);
  if (activeTabId !== revealedFor && roots.length > 0) {
    setRevealedFor(activeTabId);
    const path = activeTabId ? pathTo(roots, activeTabId) : [];
    if (path.some((id) => !open.has(id))) setExpanded(new Set([...open, ...path]));
  }

  const toggle = (node: TreeNode) =>
    setExpanded((current) => {
      const next = new Set(current ?? defaultExpanded(roots));
      if (next.has(node.id)) next.delete(node.id);
      else next.add(node.id);
      return next;
    });

  const activate = (node: TreeNode) => {
    setFocusedId(node.id);
    if (node.method_id) {
      openMethod(node.method_id);
      selectNode(node.method_id, "tree", node.range);
      return;
    }
    if (node.children.length > 0) toggle(node);
    if (node.range) {
      setActiveFile(node.range.file);
      selectNode(node.node_id ?? node.id, "tree", node.range);
    }
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    const index = rows.findIndex((row) => row.node.id === focusedId);
    const current = rows[index];
    const move = (to: number) => {
      const row = rows[Math.max(0, Math.min(rows.length - 1, to))];
      if (!row) return;
      setFocusedId(row.node.id);
      listRef.current?.querySelector<HTMLElement>(`[data-row-id="${CSS.escape(row.node.id)}"]`)?.focus();
    };
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        move(index + 1);
        break;
      case "ArrowUp":
        event.preventDefault();
        move(index - 1);
        break;
      case "ArrowRight":
        if (current && current.node.children.length > 0 && !current.expanded) toggle(current.node);
        else move(index + 1);
        event.preventDefault();
        break;
      case "ArrowLeft":
        if (current?.expanded) toggle(current.node);
        event.preventDefault();
        break;
      case "Enter":
        if (current) activate(current.node);
        break;
    }
  };

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label={t.title}>
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <h2 className="text-sm font-semibold">{t.title}</h2>
      </div>
      <div className="relative px-2 py-2">
        <Search className="pointer-events-none absolute top-1/2 left-4 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder={t.search}
          aria-label={t.search}
          className="h-8 pl-7 text-xs"
        />
      </div>

      {tree.isLoading && (
        <div className="space-y-2 p-3">
          <Skeleton className="h-5 w-full" />
          <Skeleton className="h-5 w-4/5" />
          <Skeleton className="h-5 w-3/5" />
        </div>
      )}
      {tree.error && <p className="p-3 text-xs text-destructive">{tree.error.message}</p>}
      {tree.data && roots.length === 0 && <p className="p-3 text-xs text-muted-foreground">{t.empty}</p>}
      {tree.data && roots.length > 0 && rows.length === 0 && (
        <p className="p-3 text-xs text-muted-foreground">{t.nothing}</p>
      )}

      <ScrollArea className="min-h-0 flex-1">
        <div ref={listRef} role="tree" aria-label={t.title} className="pb-2" onKeyDown={onKeyDown}>
          {rows.map((row) => (
            <TreeRowView
              key={row.node.id}
              row={row}
              active={row.node.id === activeTabId || row.node.id === selectedId}
              focused={row.node.id === focusedId || (focusedId === null && row === rows[0])}
              onActivate={() => activate(row.node)}
              onToggle={() => toggle(row.node)}
              onOpenStructure={() => openStructure()}
            />
          ))}
        </div>
      </ScrollArea>
    </section>
  );
}

function TreeRowView({
  row,
  active,
  focused,
  onActivate,
  onToggle,
  onOpenStructure,
}: {
  row: TreeRow;
  active: boolean;
  focused: boolean;
  onActivate: () => void;
  onToggle: () => void;
  onOpenStructure: () => void;
}) {
  const { node, depth, expanded, matched } = row;
  const hasChildren = node.children.length > 0;
  const isType = node.kind === "type";
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <div
          role="treeitem"
          tabIndex={focused ? 0 : -1}
          aria-level={depth + 1}
          aria-expanded={hasChildren ? expanded : undefined}
          aria-selected={active}
          data-row-id={node.id}
          title={node.name}
          className={cn(
            "flex cursor-pointer items-center gap-1 py-1 pr-2 text-xs outline-none hover:bg-accent/60 focus-visible:bg-accent focus-visible:ring-1 focus-visible:ring-ring",
            active && "bg-accent text-accent-foreground",
            matched && "font-semibold",
          )}
          style={{ paddingLeft: 8 + depth * 14 }}
          onClick={onActivate}
          onDoubleClick={() => {
            if (hasChildren && node.method_id === null) onToggle();
          }}
        >
          <button
            type="button"
            tabIndex={-1}
            aria-hidden={!hasChildren}
            className={cn("flex size-4 shrink-0 items-center justify-center", !hasChildren && "invisible")}
            onClick={(event) => {
              event.stopPropagation();
              onToggle();
            }}
          >
            {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          </button>
          <NodeIcon node={node} />
          <span className="min-w-0 flex-1 truncate">{node.name}</span>
          {node.complexity && node.method_id && (
            <span className="shrink-0 rounded bg-primary/15 px-1 font-mono text-[10px] text-primary">
              {node.complexity}
            </span>
          )}
          <FindingDots node={node} />
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent>
        {node.method_id && <ContextMenuItem onSelect={onActivate}>{t.openMethod}</ContextMenuItem>}
        {(isType || node.kind === "file") && (
          <ContextMenuItem onSelect={onOpenStructure}>{t.showStructure}</ContextMenuItem>
        )}
        {!node.method_id && !isType && node.kind !== "file" && (
          <ContextMenuItem disabled>{ru.header.soonEdit}</ContextMenuItem>
        )}
      </ContextMenuContent>
    </ContextMenu>
  );
}
