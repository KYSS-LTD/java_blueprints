import { QueryClient } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "@/lib/api/schema";
import { ApiError, fileQuery, flowQuery, projectQuery } from "@/lib/api/queries";
import { useEditorStore } from "@/stores/editor-store";

type Project = components["schemas"]["ProjectDetail"];
type FlowGraph = components["schemas"]["FlowGraph"];

const api = vi.hoisted(() => ({
  postOps: vi.fn(),
  saveFile: vi.fn(),
  undoVersion: vi.fn(),
  redoVersion: vi.fn(),
  restoreVersion: vi.fn(),
}));
vi.mock("@/lib/api/mutations", () => api);

import { adoptHead, applyOps, flushDrafts, headOf, moveHead, runExclusive } from "./session";

const METHOD = "Main.java#Main.run()";
const version = (id: number) => ({ id, number: id, origin: "graph_op", description: "", parent_id: id - 1 });
const flowOf = (versionId: number): FlowGraph =>
  ({ method_id: METHOD, file: "Main.java", version_id: versionId, nodes: [], edges: [] }) as unknown as FlowGraph;

function setup(head = 5) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  queryClient.setQueryData(projectQuery(1).queryKey, { id: 1, name: "demo", head_version_id: head } as Project);
  return queryClient;
}

describe("runExclusive", () => {
  it("задачи проекта выполняются по очереди, даже если первая медленная или упала", async () => {
    const order: string[] = [];
    const slow = runExclusive(1, async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
      order.push("slow");
    });
    const failing = runExclusive(1, async () => {
      order.push("failing");
      throw new Error("сбой");
    });
    const last = runExclusive(1, async () => void order.push("last"));
    await slow;
    await expect(failing).rejects.toThrow("сбой");
    await last;
    expect(order).toEqual(["slow", "failing", "last"]);
  });
});

describe("applyOps", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useEditorStore.getState().reset();
  });

  it("отправляет пакет на актуальный HEAD и сразу переводит кеш на новую версию", async () => {
    const queryClient = setup(5);
    api.postOps.mockResolvedValue({
      version: version(6),
      changed_files: ["Main.java"],
      focus_node_id: `${METHOD}/op[0]`,
      id_map: {},
      flow: flowOf(6),
      syntax_errors: [],
    });
    const ops = [{ op: "delete_node", node_id: `${METHOD}/op[0]`, mode: "with_body" }] as never;

    await applyOps(queryClient, 1, ops, "Удалить блок");

    expect(api.postOps).toHaveBeenCalledWith(1, 5, ops, "Удалить блок");
    expect(headOf(queryClient, 1)).toBe(6);
    // схема новой версии уже в кеше: холст не мигает
    expect(queryClient.getQueryData(flowQuery(1, METHOD, 6).queryKey)).toEqual(flowOf(6));
    expect(useEditorStore.getState().busy).toBe(0);
  });

  it("сначала сохраняет набранный код: операция идёт на версию, созданную сохранением", async () => {
    const queryClient = setup(5);
    useEditorStore.getState().setDraft("Main.java", "class Main {}");
    api.saveFile.mockResolvedValue({ head_version_id: 6, syntax_errors: [], changed_files: ["Main.java"] });
    api.postOps.mockResolvedValue({
      version: version(7),
      changed_files: [],
      focus_node_id: null,
      id_map: {},
      flow: null,
      syntax_errors: [],
    });

    await applyOps(queryClient, 1, [{ op: "duplicate_node", node_id: "x" }] as never);

    expect(api.saveFile).toHaveBeenCalledWith(1, "Main.java", 5, "class Main {}");
    expect(api.postOps.mock.calls[0][1]).toBe(6);
    expect(useEditorStore.getState().drafts).toEqual({});
  });

  it("при 409 проект перечитывается, ошибка уходит вызывающему", async () => {
    const queryClient = setup(5);
    api.postOps.mockRejectedValue(new ApiError("версия устарела", "VERSION_CONFLICT"));

    await expect(applyOps(queryClient, 1, [{ op: "duplicate_node", node_id: "x" }] as never)).rejects.toMatchObject({
      code: "VERSION_CONFLICT",
    });
    expect(queryClient.getQueryState(projectQuery(1).queryKey)?.isInvalidated).toBe(true);
    expect(useEditorStore.getState().busy).toBe(0);
  });
});

describe("flushDrafts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useEditorStore.getState().reset();
  });

  it("сохраняет файл, кладёт его текст и схему в кеш новой версии и снимает черновик", async () => {
    const queryClient = setup(5);
    const store = useEditorStore.getState();
    store.setDraft("Main.java", "новый текст");
    store.applyParse("Main.java", METHOD, "новый текст", [], flowOf(5));
    api.saveFile.mockResolvedValue({ head_version_id: 6, syntax_errors: [], changed_files: ["Main.java"] });

    await flushDrafts(queryClient, 1);

    expect(api.saveFile).toHaveBeenCalledWith(1, "Main.java", 5, "новый текст");
    expect(headOf(queryClient, 1)).toBe(6);
    expect(queryClient.getQueryData(fileQuery(1, "Main.java", 6).queryKey)).toMatchObject({
      content: "новый текст",
      version_id: 6,
    });
    expect(queryClient.getQueryData(flowQuery(1, METHOD, 6).queryKey)).toMatchObject({ version_id: 6 });
    expect(useEditorStore.getState().drafts).toEqual({});
    expect(useEditorStore.getState().saveState).toBe("idle");
  });

  it("код с синтаксической ошибкой: холст остаётся на последней корректной схеме", async () => {
    const queryClient = setup(5);
    queryClient.setQueryData(flowQuery(1, METHOD, 5).queryKey, flowOf(5));
    useEditorStore.getState().setDraft("Main.java", "int q = ;");
    api.saveFile.mockResolvedValue({
      head_version_id: 6,
      changed_files: ["Main.java"],
      syntax_errors: [{ message: "ошибка", range: { file: "Main.java", start_line: 1, start_col: 1, end_line: 1, end_col: 2 } }],
    });

    await flushDrafts(queryClient, 1);

    expect(queryClient.getQueryData(flowQuery(1, METHOD, 6).queryKey)).toMatchObject({ version_id: 6, nodes: [] });
    expect(queryClient.getQueryData(fileQuery(1, "Main.java", 6).queryKey)).toMatchObject({
      syntax_errors: [{ message: "ошибка" }],
    });
  });

  it("ошибка сохранения отражается в состоянии редактора, черновик не теряется", async () => {
    const queryClient = setup(5);
    useEditorStore.getState().setDraft("Main.java", "текст");
    api.saveFile.mockRejectedValue(new ApiError("нет связи"));

    await expect(flushDrafts(queryClient, 1)).rejects.toThrow("нет связи");

    const state = useEditorStore.getState();
    expect(state.saveState).toBe("error");
    expect(state.saveError).toBe("нет связи");
    expect(state.drafts["Main.java"]).toBe("текст");
  });
});

describe("moveHead и adoptHead", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useEditorStore.getState().reset();
  });

  it("отмена переводит HEAD на версию из ответа и сбрасывает черновики", async () => {
    const queryClient = setup(5);
    api.undoVersion.mockResolvedValue({ head_version_id: 4, version: version(4), changed_files: [], syntax_errors: [] });

    await moveHead(queryClient, 1, "undo");

    expect(api.undoVersion).toHaveBeenCalledWith(1);
    expect(headOf(queryClient, 1)).toBe(4);
  });

  it("восстановление версии вызывает restore с её id", async () => {
    const queryClient = setup(5);
    api.restoreVersion.mockResolvedValue({ head_version_id: 7, version: version(7), changed_files: [], syntax_errors: [] });
    await moveHead(queryClient, 1, { restore: 2 });
    expect(api.restoreVersion).toHaveBeenCalledWith(1, 2);
  });

  it("adoptHead помечает данные версий устаревшими, кроме положенных в кеш самим клиентом", () => {
    const queryClient = setup(5);
    const kept = flowQuery(1, METHOD, 6).queryKey;
    const stale = flowQuery(1, "Main.java#Main.other()", 5).queryKey;
    queryClient.setQueryData(kept, flowOf(6));
    queryClient.setQueryData(stale, flowOf(5));

    adoptHead(queryClient, 1, 6, [kept]);

    expect(queryClient.getQueryState(kept)?.isInvalidated).toBe(false);
    expect(queryClient.getQueryState(stale)?.isInvalidated).toBe(true);
  });
});
