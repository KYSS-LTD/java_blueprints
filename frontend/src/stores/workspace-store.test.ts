import { beforeEach, describe, expect, it } from "vitest";

import {
  fileOfMethod,
  formatWorkspaceLocation,
  methodTitle,
  parseWorkspaceLocation,
  useWorkspaceStore,
} from "./workspace-store";

const run = "Main.java#Main.run(Scanner)";
const total = "Main.java#Order.getTotal()";

beforeEach(() => useWorkspaceStore.getState().reset());

describe("workspaceStore", () => {
  it("открывает методы во вкладках без дублей и переключает файл кода", () => {
    const store = useWorkspaceStore.getState();
    store.openMethod(run);
    store.openMethod(total);
    store.openMethod(run);
    const state = useWorkspaceStore.getState();
    expect(state.tabs.map((t) => t.title)).toEqual(["run(Scanner)", "getTotal()"]);
    expect(state.activeTabId).toBe(run);
    expect(state.activeFile).toBe("Main.java");
  });

  it("закрытие активной вкладки активирует соседнюю, последней — ничего", () => {
    const store = useWorkspaceStore.getState();
    store.openMethod(run);
    store.openMethod(total);
    store.closeTab(total);
    expect(useWorkspaceStore.getState().activeTabId).toBe(run);
    store.closeTab(run);
    expect(useWorkspaceStore.getState().activeTabId).toBeNull();
    expect(useWorkspaceStore.getState().tabs).toEqual([]);
  });

  it("повторный выбор того же узла получает новый seq", () => {
    const store = useWorkspaceStore.getState();
    store.selectNode("n1", "graph");
    const first = useWorkspaceStore.getState().selection!;
    store.selectNode("n1", "graph");
    const second = useWorkspaceStore.getState().selection!;
    expect(second.nodeId).toBe(first.nodeId);
    expect(second.seq).toBeGreaterThan(first.seq);
    store.selectNode(null, "graph");
    expect(useWorkspaceStore.getState().selection).toBeNull();
  });

  it("запоминает ручную свёрнутость контейнеров отдельно для каждого метода", () => {
    const store = useWorkspaceStore.getState();
    store.setCollapsed(run, "loop", true);
    store.setCollapsed(total, "loop", false);
    expect(useWorkspaceStore.getState().collapsedOverrides).toEqual({
      [run]: { loop: true },
      [total]: { loop: false },
    });
  });
});

describe("URL рабочей области", () => {
  it("разбирает и собирает параметры file, method, node", () => {
    const location = { file: "Main.java", method: run, node: `${run}/foreach[0]` };
    const search = formatWorkspaceLocation(location);
    expect(parseWorkspaceLocation(search)).toEqual(location);
    expect(formatWorkspaceLocation({ file: null, method: null, node: null })).toBe("");
  });

  it("называет метод и файл по id члена", () => {
    expect(methodTitle(run)).toBe("run(Scanner)");
    expect(methodTitle("Main.java#Main.topN(List<T>, Comparator<? super T>, int)")).toBe(
      "topN(List<T>, Comparator<? super T>, int)",
    );
    expect(fileOfMethod(total)).toBe("Main.java");
  });
});
