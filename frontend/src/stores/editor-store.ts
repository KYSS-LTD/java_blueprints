import { create } from "zustand";

import type { GraphOp } from "@/lib/api/mutations";
import type { components } from "@/lib/api/schema";
import type { PaletteField } from "@/lib/edit/block-fields";
import type { InsertPoint } from "@/lib/edit/slots";
import type { BlockKind, FlowGraph, NodeKind } from "@/lib/graph/flow-model";

type SyntaxIssue = components["schemas"]["SyntaxIssue"];

/** Состояние автосохранения кода: пауза после ввода → запрос → готово. */
export type SaveState = "idle" | "dirty" | "saving" | "error";

/** Что открыто в инспекторе справа от холста (docs/09 § 3.4). */
export type InspectorTarget =
  | { mode: "update"; methodId: string; nodeId: string }
  | { mode: "insert"; methodId: string; kind: NodeKind; point: InsertPoint }
  | {
      /** Действие над блоком, которому нужны поля: обернуть, добавить ветку `else if`/`catch`/`case`. */
      mode: "action";
      methodId: string;
      /** Ключ, по которому форма сбрасывается при смене действия. */
      key: string;
      /** Вид блока, по которому выбираются цвет и значок. */
      kind: BlockKind;
      title: string;
      fields: PaletteField[];
      /** Узел, на который действует операция (для проверки имён в его области видимости). */
      contextId: string;
      description: string;
      makeOp: (fields: Record<string, string | boolean>) => GraphOp;
    };

type EditorState = {
  /** Несохранённый текст файлов: то, что сейчас в Monaco, если оно ушло вперёд сохранённого. */
  drafts: Record<string, string>;
  /** Синтаксические ошибки черновика по живому разбору. */
  issues: Record<string, SyntaxIssue[]>;
  /** Последняя корректная схема метода по живому разбору черновика. */
  liveFlows: Record<string, FlowGraph>;
  /** Текст, из которого получена схема `liveFlows[methodId]`. */
  parsedText: Record<string, string>;
  saveState: SaveState;
  saveError: string | null;
  inspector: InspectorTarget | null;
  /** Сколько операций над проектом выполняется (графа, отмена): пока их больше нуля, код не правится. */
  busy: number;

  reset: () => void;
  beginBusy: () => void;
  endBusy: () => void;
  setDraft: (file: string, text: string) => void;
  applyParse: (file: string, methodId: string | null, text: string, issues: SyntaxIssue[], flow: FlowGraph | null) => void;
  setSaveState: (state: SaveState, error?: string | null) => void;
  /** Текст `text` сохранён: черновик снимается, если за время запроса он не ушёл вперёд. */
  finishSave: (file: string, text: string, issues: SyntaxIssue[]) => void;
  /** Схемы по живому разбору больше не нужны (HEAD сменила операция, отмена или восстановление). */
  dropDrafts: () => void;
  openInspector: (target: InspectorTarget | null) => void;
};

const initial = {
  drafts: {} as Record<string, string>,
  issues: {} as Record<string, SyntaxIssue[]>,
  liveFlows: {} as Record<string, FlowGraph>,
  parsedText: {} as Record<string, string>,
  saveState: "idle" as SaveState,
  saveError: null as string | null,
  inspector: null as InspectorTarget | null,
  busy: 0,
};

function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  const { [key]: _removed, ...rest } = record;
  void _removed;
  return rest;
}

export const useEditorStore = create<EditorState>((set) => ({
  ...initial,

  reset: () => set({ ...initial }),

  beginBusy: () => set((state) => ({ busy: state.busy + 1 })),
  endBusy: () => set((state) => ({ busy: Math.max(0, state.busy - 1) })),

  setDraft: (file, text) =>
    set((state) => ({
      drafts: { ...state.drafts, [file]: text },
      saveState: state.saveState === "saving" ? "saving" : "dirty",
    })),

  applyParse: (file, methodId, text, issues, flow) =>
    set((state) => {
      // ответ запоздал: пользователь уже набрал дальше
      if (state.drafts[file] !== text) return state;
      const next: Partial<EditorState> = { issues: { ...state.issues, [file]: issues } };
      if (issues.length === 0 && methodId && flow) {
        next.liveFlows = { ...state.liveFlows, [methodId]: flow };
        next.parsedText = { ...state.parsedText, [methodId]: text };
      }
      return next;
    }),

  setSaveState: (saveState, error = null) => set({ saveState, saveError: error }),

  finishSave: (file, text, issues) =>
    set((state) => {
      const caughtUp = state.drafts[file] === text;
      if (!caughtUp) return { saveState: "dirty", saveError: null };
      const drafts = without(state.drafts, file);
      const next: Partial<EditorState> = {
        drafts,
        issues: without(state.issues, file),
        saveState: Object.keys(drafts).length > 0 ? "dirty" : "idle",
        saveError: null,
      };
      // сохранённый корректный текст — это уже HEAD: запросы вернут ту же схему, запасная не нужна
      if (issues.length === 0) {
        next.liveFlows = Object.fromEntries(
          Object.entries(state.liveFlows).filter(([, flow]) => flow.file !== file),
        );
        next.parsedText = Object.fromEntries(
          Object.entries(state.parsedText).filter(([id]) => !id.startsWith(`${file}#`)),
        );
      }
      return next;
    }),

  dropDrafts: () => set({ drafts: {}, issues: {}, liveFlows: {}, parsedText: {}, saveState: "idle", saveError: null }),

  openInspector: (inspector) => set({ inspector }),
}));
