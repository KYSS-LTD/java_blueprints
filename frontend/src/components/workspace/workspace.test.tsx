import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { ru } from "@/lib/i18n/ru";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

// Monaco не работает в jsdom: заменяем редактор заглушкой, которая показывает, что ему передали.
type EditorProps = {
  path: string;
  value: string;
  highlight: { start_line: number } | null;
  revealSeq: number | null;
  onCursor?: (line: number, column: number) => void;
};
const editor: { props: EditorProps | null } = { props: null };

vi.mock("@/components/code/code-editor", () => ({
  default: (props: EditorProps) => {
    editor.props = props;
    return (
      <pre data-testid="editor-stub" data-path={props.path} data-highlight={props.highlight?.start_line ?? ""}>
        {props.value.slice(0, 40)}
      </pre>
    );
  },
}));

async function renderWorkspace() {
  const { Workspace } = await import("@/components/workspace/workspace");
  const { useWorkspaceStore } = await import("@/stores/workspace-store");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <Workspace projectId={1} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return { store: useWorkspaceStore };
}

describe("Workspace на моках", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_MOCK", "1");
    vi.resetModules();
    window.history.replaceState(null, "", "/projects/1");
  });
  afterEach(() => vi.unstubAllEnvs());

  it("показывает шапку, дерево проекта, палитру и панель анализа", async () => {
    await renderWorkspace();
    expect(await screen.findByRole("heading", { name: "demo-orders" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: new RegExp(ru.header.run) })).toBeInTheDocument();
    expect(await screen.findByRole("treeitem", { name: "Main.java" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: ru.palette.title })).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: ru.analysis.title })).toBeInTheDocument();
    expect(screen.getByText(ru.workspace.emptyCenter)).toBeInTheDocument();
  });

  it("«Код Java», «Консоль» и «Результат тестов» стоят вкладками, пока области не хватает ширины", async () => {
    await renderWorkspace();
    // в jsdom ширина областей нулевая
    expect(await screen.findByRole("tab", { name: ru.console.title })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: ru.console.title })).not.toBeInTheDocument();
  });

  it("и рядом, как в макете, когда ширины хватает", async () => {
    const measure = vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 900,
      bottom: 300,
      width: 900,
      height: 300,
      toJSON: () => ({}),
    });
    try {
      await renderWorkspace();
      for (const title of [ru.code.title, ru.console.title, ru.tests.title]) {
        expect(await screen.findByRole("region", { name: title })).toBeInTheDocument();
      }
      expect(screen.queryByRole("tab", { name: ru.console.title })).not.toBeInTheDocument();
    } finally {
      measure.mockRestore();
    }
  });

  it("клик по методу открывает вкладку, схему и файл в редакторе", async () => {
    const user = userEvent.setup();
    const { store } = await renderWorkspace();
    // тип Main раскрывается кликом по строке
    await user.click(await screen.findByRole("treeitem", { name: "Main" }));
    const method = await screen.findByRole("treeitem", { name: /^mergeDuplicates\(\)/ });
    await user.click(method);

    expect(await screen.findByRole("tab", { name: /mergeDuplicates\(\)/ })).toHaveAttribute("aria-selected", "true");
    expect(store.getState().activeFile).toBe("Main.java");
    expect(await screen.findByTestId("flow-canvas")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByTestId(/^block-/).length).toBeGreaterThan(5), { timeout: 8000 });
    // у mergeDuplicates вложенные циклы: контейнеров несколько
    expect(screen.getAllByTestId("container-loop_foreach").length).toBeGreaterThanOrEqual(2);
    expect((await screen.findByTestId("editor-stub")).getAttribute("data-path")).toBe("Main.java");
  }, 20_000);

  it("открывается сразу на нужном методе по параметрам URL", async () => {
    const method = encodeURIComponent("Main.java#Main.buildCsv()");
    window.history.replaceState(null, "", "/projects/1?file=Main.java&method=" + method);
    const { store } = await renderWorkspace();
    expect(await screen.findByRole("tab", { name: /buildCsv\(\)/ })).toBeInTheDocument();
    expect(store.getState().activeTabId).toBe("Main.java#Main.buildCsv()");
    await waitFor(() => expect(screen.getAllByTestId(/^block-/).length).toBeGreaterThan(3), { timeout: 8000 });
  }, 20_000);

  it("вкладку можно закрыть, схема пропадает", async () => {
    const user = userEvent.setup();
    const method = encodeURIComponent("Main.java#Main.buildCsv()");
    window.history.replaceState(null, "", "/projects/1?method=" + method);
    await renderWorkspace();
    await screen.findByRole("tab", { name: /buildCsv\(\)/ });
    await user.click(screen.getByLabelText(/Закрыть вкладку: buildCsv\(\)/)); // кнопка скрыта от AT, закрывает и Delete
    expect(screen.queryByRole("tab", { name: /buildCsv\(\)/ })).not.toBeInTheDocument();
    expect(screen.getByText(ru.workspace.emptyCenter)).toBeInTheDocument();
  }, 20_000);

  it("курсор в коде выбирает блок схемы, а клик по блоку подсвечивает код", async () => {
    const method = encodeURIComponent("Main.java#Main.buildCsv()");
    window.history.replaceState(null, "", "/projects/1?file=Main.java&method=" + method);
    const { store } = await renderWorkspace();
    await waitFor(() => expect(screen.getAllByTestId(/^block-/).length).toBeGreaterThan(3), { timeout: 8000 });
    await screen.findByTestId("editor-stub");

    // курсор в теле метода: backend возвращает диапазоны блоков, ищем самый глубокий
    const flowModule = await import("@/mocks/fixtures/flows.json");
    const flow = (flowModule.default as unknown as Record<string, { nodes: Array<{ id: string; kind: string; range: { start_line: number; start_col: number } }> }>)[
      "Main.java#Main.buildCsv()"
    ];
    const loop = flow.nodes.find((n) => n.kind === "loop_foreach")!;
    await waitFor(() => expect(editor.props?.onCursor).toBeDefined());
    editor.props!.onCursor!(loop.range.start_line, loop.range.start_col);
    await waitFor(() => expect(store.getState().selection?.nodeId).toBe(loop.id));
    expect(store.getState().selection?.source).toBe("code");

    // клик по блоку на холсте: код получает диапазон и просьбу прокрутить
    const user = userEvent.setup();
    await user.click(screen.getAllByTestId(/^block-var_decl/)[0]);
    await waitFor(() => expect(store.getState().selection?.source).toBe("graph"));
    await waitFor(() => expect(editor.props?.highlight).not.toBeNull());
    expect(editor.props?.revealSeq).toBe(store.getState().selection?.seq);
  }, 20_000);
});
