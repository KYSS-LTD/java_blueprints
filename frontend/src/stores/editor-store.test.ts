import { beforeEach, describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";

import { useEditorStore } from "./editor-store";

type FlowGraph = components["schemas"]["FlowGraph"];
type Issue = components["schemas"]["SyntaxIssue"];

const issue: Issue = {
  message: "Синтаксическая ошибка",
  range: { file: "Main.java", start_line: 3, start_col: 1, end_line: 3, end_col: 5 },
};
const flow = { method_id: "Main.java#Main.run()", file: "Main.java", nodes: [], edges: [] } as unknown as FlowGraph;

describe("editorStore", () => {
  beforeEach(() => useEditorStore.getState().reset());

  it("ввод создаёт черновик и помечает файл несохранённым", () => {
    useEditorStore.getState().setDraft("Main.java", "class A {}");
    const state = useEditorStore.getState();
    expect(state.drafts).toEqual({ "Main.java": "class A {}" });
    expect(state.saveState).toBe("dirty");
  });

  it("живой разбор без ошибок запоминает схему, с ошибками — оставляет последнюю корректную", () => {
    const store = useEditorStore.getState();
    store.setDraft("Main.java", "v1");
    store.applyParse("Main.java", "Main.java#Main.run()", "v1", [], flow);
    expect(useEditorStore.getState().liveFlows["Main.java#Main.run()"]).toBe(flow);

    store.setDraft("Main.java", "v2");
    store.applyParse("Main.java", "Main.java#Main.run()", "v2", [issue], { ...flow, nodes: [] });
    const state = useEditorStore.getState();
    expect(state.issues["Main.java"]).toEqual([issue]);
    expect(state.liveFlows["Main.java#Main.run()"]).toBe(flow); // схема версии v1
    expect(state.parsedText["Main.java#Main.run()"]).toBe("v1");
  });

  it("запоздавший ответ разбора отбрасывается: пользователь уже набрал дальше", () => {
    const store = useEditorStore.getState();
    store.setDraft("Main.java", "новый текст");
    store.applyParse("Main.java", "Main.java#Main.run()", "старый текст", [issue], flow);
    expect(useEditorStore.getState().issues).toEqual({});
    expect(useEditorStore.getState().liveFlows).toEqual({});
  });

  it("сохранённый корректный текст снимает черновик и запасные схемы", () => {
    const store = useEditorStore.getState();
    store.setDraft("Main.java", "v1");
    store.applyParse("Main.java", "Main.java#Main.run()", "v1", [], flow);
    store.setSaveState("saving");
    store.finishSave("Main.java", "v1", []);
    const state = useEditorStore.getState();
    expect(state.drafts).toEqual({});
    expect(state.liveFlows).toEqual({});
    expect(state.saveState).toBe("idle");
  });

  it("если пока шёл запрос, текст ушёл вперёд, черновик остаётся", () => {
    const store = useEditorStore.getState();
    store.setDraft("Main.java", "v1");
    store.setSaveState("saving");
    store.setDraft("Main.java", "v2");
    store.finishSave("Main.java", "v1", []);
    const state = useEditorStore.getState();
    expect(state.drafts["Main.java"]).toBe("v2");
    expect(state.saveState).toBe("dirty");
  });

  it("сохранённый текст с ошибкой оставляет последнюю корректную схему", () => {
    const store = useEditorStore.getState();
    store.setDraft("Main.java", "v1");
    store.applyParse("Main.java", "Main.java#Main.run()", "v1", [], flow);
    store.setDraft("Main.java", "сломано");
    store.finishSave("Main.java", "сломано", [issue]);
    expect(useEditorStore.getState().liveFlows["Main.java#Main.run()"]).toBe(flow);
    expect(useEditorStore.getState().drafts).toEqual({});
  });

  it("операция над проектом блокирует редактор, пока выполняется", () => {
    const store = useEditorStore.getState();
    store.beginBusy();
    store.beginBusy();
    store.endBusy();
    expect(useEditorStore.getState().busy).toBe(1);
    store.endBusy();
    store.endBusy();
    expect(useEditorStore.getState().busy).toBe(0);
  });

  it("dropDrafts сбрасывает всё, что относилось к прежнему HEAD", () => {
    const store = useEditorStore.getState();
    store.setDraft("Main.java", "v1");
    store.applyParse("Main.java", "Main.java#Main.run()", "v1", [issue], flow);
    store.dropDrafts();
    const state = useEditorStore.getState();
    expect(state.drafts).toEqual({});
    expect(state.issues).toEqual({});
    expect(state.saveState).toBe("idle");
  });
});
