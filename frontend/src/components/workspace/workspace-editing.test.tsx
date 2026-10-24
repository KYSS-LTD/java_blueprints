import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

// Monaco не работает в jsdom: редактор заменяется заглушкой с кнопкой «ввод».
type EditorProps = { value: string; readOnly?: boolean; onChange?: (text: string) => void; onSave?: () => void };
const editor: { props: EditorProps | null } = { props: null };
vi.mock("@/components/code/code-editor", () => ({
  default: (props: EditorProps) => {
    editor.props = props;
    return <pre data-testid="editor-stub" data-readonly={String(props.readOnly)} />;
  },
}));

// Операции над проектом идут на сервер: подменяем клиент, остальное работает на моках (NEXT_PUBLIC_MOCK).
const api = vi.hoisted(() => ({
  postOps: vi.fn(),
  validateOps: vi.fn(),
  checkFragment: vi.fn(),
  undoVersion: vi.fn(),
  redoVersion: vi.fn(),
  restoreVersion: vi.fn(),
  saveFile: vi.fn(),
  parseLive: vi.fn(),
}));
vi.mock("@/lib/api/mutations", () => api);

const METHOD = "Main.java#Main.buildCsv()";

const opsResult = {
  version: { id: 4, number: 4, origin: "graph_op", description: "", parent_id: 3, created_at: "2026-10-24T09:00:00Z", gate_status: "unknown", status: "active", content_hash: "x" },
  changed_files: ["Main.java"],
  focus_node_id: null,
  id_map: {},
  flow: null,
  syntax_errors: [],
};

async function renderWorkspace() {
  const { Workspace } = await import("@/components/workspace/workspace");
  const { useWorkspaceStore } = await import("@/stores/workspace-store");
  const { useEditorStore } = await import("@/stores/editor-store");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <Workspace projectId={1} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return { workspace: useWorkspaceStore, editorStore: useEditorStore, client };
}

describe("правка в рабочей области (моки + подменённые операции)", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_MOCK", "1");
    vi.resetModules();
    vi.clearAllMocks();
    api.postOps.mockResolvedValue(opsResult);
    api.validateOps.mockResolvedValue({ ok: true, errors: [], diff: [] });
    api.checkFragment.mockResolvedValue({ ok: true, message: null });
    window.history.replaceState(null, "", `/projects/1?file=Main.java&method=${encodeURIComponent(METHOD)}`);
  });
  afterEach(() => vi.unstubAllEnvs());

  it("отмена и повтор доступны по истории версий; отмена вызывает API", async () => {
    const user = userEvent.setup();
    api.undoVersion.mockResolvedValue({ head_version_id: 2, version: opsResult.version, changed_files: [], syntax_errors: [] });
    await renderWorkspace();

    const undo = await screen.findByTestId("undo-button");
    await waitFor(() => expect(undo).toBeEnabled()); // у HEAD (v3) есть родитель
    expect(screen.getByTestId("redo-button")).toBeDisabled(); // потомков нет
    await user.click(undo);
    await waitFor(() => expect(api.undoVersion).toHaveBeenCalledWith(1));
  });

  it("история версий показывает происхождение и восстанавливает выбранную", async () => {
    const user = userEvent.setup();
    api.restoreVersion.mockResolvedValue({ head_version_id: 1, version: opsResult.version, changed_files: [], syntax_errors: [] });
    await renderWorkspace();

    await user.click(await screen.findByTestId("history-button"));
    const row = await screen.findByTestId("version-2");
    expect(row).toHaveTextContent("Правка графа");
    expect(screen.getByTestId("version-3")).toHaveTextContent("текущая");
    await user.click(row.querySelector("button:last-of-type")!);
    await waitFor(() => expect(api.restoreVersion).toHaveBeenCalledWith(1, 2));
  });

  it("клик по блоку палитры без полей вставляет его сразу после выбранного", async () => {
    const user = userEvent.setup();
    await renderWorkspace();
    await waitFor(() => expect(screen.getAllByTestId(/^block-/).length).toBeGreaterThan(3), { timeout: 8000 });

    await user.click(screen.getByTestId("block-var_decl"));
    await user.click(await screen.findByTestId("palette-operation"));

    // у «Операции» есть обязательное поле, поэтому открывается инспектор с целевой позицией после var_decl
    expect(await screen.findByTestId("node-inspector")).toBeInTheDocument();
    const inspector = (await import("@/stores/editor-store")).useEditorStore.getState().inspector;
    expect(inspector).toMatchObject({
      mode: "insert",
      kind: "operation",
      point: { parentId: METHOD, slot: "body", index: 1 },
    });
  }, 20_000);

  it("Delete удаляет выбранный блок, Ctrl+D дублирует его", async () => {
    const user = userEvent.setup();
    await renderWorkspace();
    await waitFor(() => expect(screen.getAllByTestId(/^block-/).length).toBeGreaterThan(3), { timeout: 8000 });
    await waitFor(() => expect(screen.getByTestId("flow-canvas")).toHaveAttribute("data-editable", "true"));

    // клавиши приходят от узла, на который перешёл фокус: в jsdom клик фокус не переносит, поэтому событие шлём сами
    await user.click(screen.getByTestId("block-assign"));
    fireEvent.keyDown(screen.getByTestId("block-assign"), { key: "d", ctrlKey: true });
    await waitFor(() => expect(api.postOps).toHaveBeenCalledTimes(1));
    expect(api.postOps.mock.calls[0][2]).toEqual([{ op: "duplicate_node", node_id: `${METHOD}/foreach[0]/assign[0]` }]);

    await user.click(screen.getByTestId("block-assign"));
    fireEvent.keyDown(screen.getByTestId("block-assign"), { key: "Delete" });
    await waitFor(() => expect(api.postOps).toHaveBeenCalledTimes(2));
    expect(api.postOps.mock.calls[1][2]).toEqual([
      { op: "delete_node", node_id: `${METHOD}/foreach[0]/assign[0]`, mode: "with_body" },
    ]);
  }, 20_000);

  it("Shift+клик выбирает подряд идущие блоки, панель открывает диалог «Выделить в метод»", async () => {
    const user = userEvent.setup();
    const { workspace } = await renderWorkspace();
    await waitFor(() => expect(screen.getAllByTestId(/^block-/).length).toBeGreaterThan(3), { timeout: 8000 });
    await waitFor(() => expect(screen.getByTestId("flow-canvas")).toHaveAttribute("data-editable", "true"));
    expect(screen.queryByTestId("selection-bar")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("block-var_decl"));
    expect(screen.queryByTestId("selection-bar")).not.toBeInTheDocument(); // один блок — не набор
    await user.keyboard("{Shift>}");
    await user.click(screen.getByTestId("container-loop_foreach"));
    await user.keyboard("{/Shift}");

    expect(workspace.getState().selectedIds).toEqual([`${METHOD}/var[0]`, `${METHOD}/foreach[0]`]);
    expect(screen.getByTestId("selection-count")).toHaveTextContent("2");

    // Delete относится к одному блоку: при наборе блоков он ничего не удаляет
    fireEvent.keyDown(screen.getByTestId("block-var_decl"), { key: "Delete" });
    expect(api.postOps).not.toHaveBeenCalled();

    // Escape сводит набор к последнему выбранному блоку, от которого Shift+клик тянет новый диапазон
    fireEvent.keyDown(screen.getByTestId("block-var_decl"), { key: "Escape" });
    expect(workspace.getState().selectedIds).toEqual([`${METHOD}/foreach[0]`]);
    expect(screen.queryByTestId("selection-bar")).not.toBeInTheDocument();
    await user.keyboard("{Shift>}");
    await user.click(screen.getByTestId("block-var_decl"));
    await user.keyboard("{/Shift}");
    expect(workspace.getState().selectedIds).toEqual([`${METHOD}/var[0]`, `${METHOD}/foreach[0]`]);

    await user.click(screen.getByTestId("selection-extract"));
    expect(workspace.getState().extractTarget).toEqual({ nodeIds: [`${METHOD}/var[0]`, `${METHOD}/foreach[0]`] });
    const dialog = await screen.findByTestId("extract-dialog");
    expect(await within(dialog).findByTestId("extract-signature")).toHaveTextContent("addLine");
  }, 30_000);

  it("Shift+клик по блоку из другого слота набор не расширяет; Ctrl+клик добавляет и убирает блок", async () => {
    const user = userEvent.setup();
    const { workspace } = await renderWorkspace();
    await waitFor(() => expect(screen.getAllByTestId(/^block-/).length).toBeGreaterThan(3), { timeout: 8000 });

    await user.click(screen.getByTestId("block-var_decl"));
    await user.keyboard("{Shift>}");
    await user.click(screen.getByTestId("block-assign")); // лежит внутри цикла, а не в теле метода
    await user.keyboard("{/Shift}");
    expect(workspace.getState().selectedIds).toEqual([`${METHOD}/foreach[0]/assign[0]`]);

    await user.keyboard("{Control>}");
    await user.click(screen.getByTestId("block-return"));
    expect(workspace.getState().selectedIds).toEqual([`${METHOD}/foreach[0]/assign[0]`, `${METHOD}/return[0]`]);
    await user.click(screen.getByTestId("block-return"));
    await user.keyboard("{/Control}");
    expect(workspace.getState().selectedIds).toEqual([`${METHOD}/foreach[0]/assign[0]`]);
  }, 30_000);

  it("контекстное меню блока предлагает «Выделить в метод» для этого блока", async () => {
    const user = userEvent.setup();
    const { workspace } = await renderWorkspace();
    await waitFor(() => expect(screen.getAllByTestId(/^block-/).length).toBeGreaterThan(3), { timeout: 8000 });
    await waitFor(() => expect(screen.getByTestId("flow-canvas")).toHaveAttribute("data-editable", "true"));

    await user.pointer({ keys: "[MouseRight]", target: screen.getByTestId("block-return") });
    await user.click(await screen.findByTestId("menu-extract"));
    expect(workspace.getState().extractTarget).toEqual({ nodeIds: [`${METHOD}/return[0]`] });
    expect(await screen.findByTestId("extract-dialog")).toBeInTheDocument();
  }, 30_000);

  it("удаление контейнера спрашивает, что делать с телом", async () => {
    const user = userEvent.setup();
    const { editorStore } = await renderWorkspace();
    await waitFor(() => expect(screen.getAllByTestId(/^container-/).length).toBeGreaterThan(0), { timeout: 8000 });
    await waitFor(() => expect(screen.getByTestId("flow-canvas")).toHaveAttribute("data-editable", "true"));

    await user.click(screen.getByTestId("container-loop_foreach"));
    fireEvent.keyDown(screen.getByTestId("container-loop_foreach"), { key: "Delete" });
    expect(await screen.findByTestId("delete-dialog")).toBeInTheDocument();
    expect(api.postOps).not.toHaveBeenCalled();
    // клавиши внутри диалога (он рисуется порталом) не должны доходить до обработчика холста: Enter открыл бы инспектор
    fireEvent.keyDown(screen.getByTestId("delete-with-body"), { key: "Enter" });
    expect(editorStore.getState().inspector).toBeNull();

    await user.click(screen.getByRole("button", { name: "Оставить тело" }));
    await waitFor(() => expect(api.postOps).toHaveBeenCalled());
    expect(api.postOps.mock.calls[0][2]).toEqual([
      { op: "delete_node", node_id: `${METHOD}/foreach[0]`, mode: "unwrap" },
    ]);
  }, 20_000);

  it("ввод в редакторе создаёт черновик, живой разбор и сохранение; редактор блокируется на время операции", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      api.parseLive.mockResolvedValue({ syntax_errors: [], flow: null });
      api.saveFile.mockResolvedValue({
        head_version_id: 3,
        version: opsResult.version,
        changed_files: ["Main.java"],
        syntax_errors: [],
        collapsed: true,
      });
      const { editorStore } = await renderWorkspace();
      await screen.findByTestId("editor-stub");
      await waitFor(() => expect(editor.props?.onChange).toBeDefined());

      editor.props!.onChange!("class Main {}");
      expect(editorStore.getState().drafts["Main.java"]).toBe("class Main {}");

      await vi.advanceTimersByTimeAsync(450);
      await waitFor(() => expect(api.parseLive).toHaveBeenCalledWith(1, "Main.java", "class Main {}", METHOD, expect.anything()));
      await vi.advanceTimersByTimeAsync(500);
      await waitFor(() => expect(api.saveFile).toHaveBeenCalledWith(1, "Main.java", 3, "class Main {}"));
      await waitFor(() => expect(editorStore.getState().drafts).toEqual({}));

      editorStore.getState().beginBusy();
      await waitFor(() => expect(screen.getByTestId("editor-stub")).toHaveAttribute("data-readonly", "true"));
      editorStore.getState().endBusy();
    } finally {
      vi.useRealTimers();
    }
  }, 30_000);
});
