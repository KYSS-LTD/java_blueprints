import { create } from "zustand";

import type { SourceRange } from "@/lib/graph/flow-model";

/** Кто выбрал узел: остальные части интерфейса реагируют на выбор, сделанный не ими. */
export type SelectionSource = "graph" | "code" | "tree" | "findings" | "url" | "structure" | "edit";

export type CenterTab =
  | { id: string; kind: "method"; methodId: string; title: string }
  | { id: "structure"; kind: "structure"; title: string }
  | { id: "decompose"; kind: "decompose"; title: string };

/** Что выделяется в новый метод: подряд идущие блоки схемы либо диапазон в коде. */
export type ExtractTarget = { nodeIds: string[] } | { range: SourceRange };

export type Selection = {
  nodeId: string;
  range: SourceRange | null;
  source: SelectionSource;
  /** Растёт при каждом выборе: повторный клик по тому же узлу тоже прокручивает код и холст. */
  seq: number;
};

export type Overlays = {
  findings: boolean;
  complexity: boolean;
  coverage: boolean;
  calls: boolean;
};

export type BottomTab = "code" | "console" | "tests";
export type RightTab = "problems" | "recommendations" | "metrics";
export type StructureMode = "types" | "methods";

export const STRUCTURE_TAB: CenterTab = { id: "structure", kind: "structure", title: "Структура" };
export const DECOMPOSE_TAB: CenterTab = { id: "decompose", kind: "decompose", title: "Декомпозиция" };

type WorkspaceState = {
  tabs: CenterTab[];
  activeTabId: string | null;
  /** Файл, который показывает панель «Код Java». */
  activeFile: string | null;
  selection: Selection | null;
  /** Блоки, выбранные вместе (Shift/Ctrl+клик); последний из них — `selection`. Пусто, если ничего не выбрано. */
  selectedIds: string[];
  /** Фрагмент, для которого открыт диалог «Выделить в метод»; `null` — диалог закрыт. */
  extractTarget: ExtractTarget | null;
  /** Ручные изменения свёрнутости контейнеров по методам: true — свёрнут, false — развёрнут. */
  collapsedOverrides: Record<string, Record<string, boolean>>;
  overlays: Overlays;
  bottomTab: BottomTab;
  rightTab: RightTab;
  structureMode: StructureMode;
  reset: () => void;
  openMethod: (methodId: string, title?: string) => void;
  openStructure: () => void;
  openDecompose: () => void;
  closeTab: (id: string) => void;
  setActiveTab: (id: string) => void;
  setActiveFile: (file: string | null) => void;
  selectNode: (nodeId: string | null, source: SelectionSource, range?: SourceRange | null) => void;
  /** Выбор нескольких блоков схемы; `primary` — тот, на котором остаётся основной выбор. */
  selectMany: (ids: string[], primary: string, range?: SourceRange | null) => void;
  /** Ctrl+клик: блок входит в выбор или выходит из него. */
  toggleSelected: (nodeId: string, range?: SourceRange | null) => void;
  openExtract: (target: ExtractTarget) => void;
  closeExtract: () => void;
  setCollapsed: (methodId: string, blockId: string, collapsed: boolean) => void;
  setOverlay: (name: keyof Overlays, value: boolean) => void;
  setBottomTab: (tab: BottomTab) => void;
  setRightTab: (tab: RightTab) => void;
  setStructureMode: (mode: StructureMode) => void;
};

const initial = {
  tabs: [] as CenterTab[],
  activeTabId: null as string | null,
  activeFile: null as string | null,
  selection: null as Selection | null,
  selectedIds: [] as string[],
  extractTarget: null as ExtractTarget | null,
  collapsedOverrides: {} as Record<string, Record<string, boolean>>,
  overlays: { findings: true, complexity: true, coverage: false, calls: true } as Overlays,
  bottomTab: "code" as BottomTab,
  rightTab: "problems" as RightTab,
  structureMode: "types" as StructureMode,
};

/** `Main.java#Main.run(Scanner)` → `run(Scanner)`; для конструктора это его имя. */
export function methodTitle(methodId: string): string {
  const member = methodId.split("#")[1] ?? methodId;
  const dot = member.lastIndexOf(".", member.indexOf("(") === -1 ? undefined : member.indexOf("("));
  return dot === -1 ? member : member.slice(dot + 1);
}

export function fileOfMethod(methodId: string): string {
  return methodId.split("#")[0];
}

let selectionSeq = 0;

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  ...initial,

  reset: () => set({ ...initial }),

  openMethod: (methodId, title) =>
    set((state) => {
      const exists = state.tabs.some((tab) => tab.id === methodId);
      const tabs = exists
        ? state.tabs
        : [...state.tabs, { id: methodId, kind: "method" as const, methodId, title: title ?? methodTitle(methodId) }];
      return { tabs, activeTabId: methodId, activeFile: fileOfMethod(methodId) };
    }),

  openStructure: () =>
    set((state) => ({
      tabs: state.tabs.some((tab) => tab.id === STRUCTURE_TAB.id) ? state.tabs : [...state.tabs, STRUCTURE_TAB],
      activeTabId: STRUCTURE_TAB.id,
    })),

  openDecompose: () =>
    set((state) => ({
      tabs: state.tabs.some((tab) => tab.id === DECOMPOSE_TAB.id) ? state.tabs : [...state.tabs, DECOMPOSE_TAB],
      activeTabId: DECOMPOSE_TAB.id,
    })),

  closeTab: (id) =>
    set((state) => {
      const index = state.tabs.findIndex((tab) => tab.id === id);
      if (index === -1) return state;
      const tabs = state.tabs.filter((tab) => tab.id !== id);
      if (state.activeTabId !== id) return { tabs };
      const next = tabs[Math.min(index, tabs.length - 1)] ?? null;
      return {
        tabs,
        activeTabId: next?.id ?? null,
        activeFile: next?.kind === "method" ? fileOfMethod(next.methodId) : state.activeFile,
      };
    }),

  setActiveTab: (id) =>
    set((state) => {
      const tab = state.tabs.find((t) => t.id === id);
      if (!tab) return state;
      return {
        activeTabId: id,
        activeFile: tab.kind === "method" ? fileOfMethod(tab.methodId) : state.activeFile,
      };
    }),

  setActiveFile: (file) => set({ activeFile: file }),

  selectNode: (nodeId, source, range = null) =>
    set({
      selection: nodeId === null ? null : { nodeId, range, source, seq: ++selectionSeq },
      selectedIds: nodeId === null ? [] : [nodeId],
    }),

  selectMany: (ids, primary, range = null) =>
    set({
      selection: { nodeId: primary, range, source: "graph", seq: ++selectionSeq },
      selectedIds: ids,
    }),

  toggleSelected: (nodeId, range = null) =>
    set((state) => {
      // выбор мог остаться от кода или дерева: он тоже входит в набор
      const current = state.selectedIds.length > 0 ? state.selectedIds : state.selection ? [state.selection.nodeId] : [];
      if (!current.includes(nodeId)) {
        return {
          selection: { nodeId, range, source: "graph", seq: ++selectionSeq },
          selectedIds: [...current, nodeId],
        };
      }
      const rest = current.filter((id) => id !== nodeId);
      const last = rest[rest.length - 1];
      return {
        selectedIds: rest,
        selection:
          last === undefined
            ? null
            : state.selection?.nodeId === nodeId
              ? { nodeId: last, range: null, source: "graph", seq: ++selectionSeq }
              : state.selection,
      };
    }),

  openExtract: (extractTarget) => set({ extractTarget }),
  closeExtract: () => set({ extractTarget: null }),

  setCollapsed: (methodId, blockId, collapsed) =>
    set((state) => ({
      collapsedOverrides: {
        ...state.collapsedOverrides,
        [methodId]: { ...state.collapsedOverrides[methodId], [blockId]: collapsed },
      },
    })),

  setOverlay: (name, value) => set((state) => ({ overlays: { ...state.overlays, [name]: value } })),
  setBottomTab: (bottomTab) => set({ bottomTab }),
  setRightTab: (rightTab) => set({ rightTab }),
  setStructureMode: (structureMode) => set({ structureMode }),
}));

export type WorkspaceLocation = { file: string | null; method: string | null; node: string | null };

/** Параметры URL рабочей области: `?file=Main.java&method=<id>&node=<id>` (docs/09 § 2). */
export function parseWorkspaceLocation(search: string): WorkspaceLocation {
  const params = new URLSearchParams(search);
  return { file: params.get("file"), method: params.get("method"), node: params.get("node") };
}

export function formatWorkspaceLocation(location: WorkspaceLocation): string {
  const params = new URLSearchParams();
  if (location.file) params.set("file", location.file);
  if (location.method) params.set("method", location.method);
  if (location.node) params.set("node", location.node);
  const text = params.toString();
  return text ? `?${text}` : "";
}
