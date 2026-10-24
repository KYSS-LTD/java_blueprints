import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { ExtractMethodPreview } from "@/lib/api/actions";
import type { RunEvent } from "@/lib/api/sse";
import { ru } from "@/lib/i18n/ru";
import project from "@/mocks/fixtures/project.json";

const emitters = new Map<number, (event: RunEvent) => void>();
vi.mock("@/lib/api/sse", () => ({
  openRunEvents: (runId: number, onEvent: (event: RunEvent) => void) => {
    emitters.set(runId, onEvent);
    return () => {};
  },
}));
vi.mock("@/components/workspace/file-diffs", () => ({
  FileDiffs: ({ diffs }: { diffs: Array<{ path: string }> }) => (
    <div data-testid="file-diffs-stub">{diffs.map((d) => d.path).join(", ")}</div>
  ),
}));

const calls = vi.hoisted(() => ({
  previewExtract: vi.fn(),
  applyExtract: vi.fn(),
  preview: { current: null as null | ((request: { name?: string | null }) => unknown) },
}));
vi.mock("@/lib/api/actions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/actions")>();
  return {
    ...actual,
    previewExtract: (...args: Parameters<typeof actual.previewExtract>) => {
      calls.previewExtract(...args);
      return calls.preview.current
        ? Promise.resolve(calls.preview.current(args[1]))
        : actual.previewExtract(...args);
    },
    applyExtract: (...args: Parameters<typeof actual.applyExtract>) => {
      calls.applyExtract(...args);
      return actual.applyExtract(...args);
    },
  };
});

const RUN = 8;
const send = (...events: RunEvent[]) =>
  act(() => {
    for (const event of events) emitters.get(RUN)?.(event);
  });

const NODES = [
  "Main.java#Main.run(Scanner)/while[0]/assign[0]",
  "Main.java#Main.run(Scanner)/while[0]/op[0]",
];

async function renderDialog(target: unknown = { nodeIds: NODES }) {
  const { ExtractDialog } = await import("@/components/workspace/extract-dialog");
  const { useWorkspaceStore } = await import("@/stores/workspace-store");
  const { useRunStore } = await import("@/stores/run-store");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <ExtractDialog projectId={1} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  act(() => useWorkspaceStore.getState().openExtract(target as never));
  return { workspace: useWorkspaceStore, runs: useRunStore };
}

const refused: ExtractMethodPreview = {
  name: "",
  signature: "",
  code: "",
  preconditions: [
    {
      name: "Нет выхода за границу (`break`/`continue`/`return`/`yield`)",
      passed: false,
      reason: "в выделенном фрагменте есть continue",
    },
  ],
  warnings: [],
  diff: [],
};

describe("ExtractDialog на моках", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_MOCK", "1");
    vi.resetModules();
    emitters.clear();
    calls.previewExtract.mockClear();
    calls.applyExtract.mockClear();
    calls.preview.current = null;
  });
  afterEach(() => vi.unstubAllEnvs());

  it("показывает имя, сигнатуру, предусловия и diff для выбранных блоков", async () => {
    await renderDialog();
    const dialog = await screen.findByTestId("extract-dialog");
    expect(await within(dialog).findByTestId("extract-signature")).toHaveTextContent(
      "void addLine(String line)",
    );
    expect(within(dialog).getByTestId("extract-name")).toHaveValue("addLine");
    expect(within(dialog).getByTestId("preconditions")).toBeInTheDocument();
    expect(within(dialog).getByTestId("file-diffs-stub")).toHaveTextContent("Main.java");
    expect(within(dialog).getByTestId("extract-apply")).toBeEnabled();
    expect(calls.previewExtract).toHaveBeenCalledTimes(1);
    expect(calls.previewExtract.mock.calls[0][1]).toMatchObject({
      node_ids: NODES,
      name: null,
      version_id: project.head_version_id,
    });
  });

  it("диапазон из кода уходит в запрос как range", async () => {
    const range = { file: "Main.java", start_line: 54, start_col: 1, end_line: 55, end_col: 30 };
    await renderDialog({ range });
    await screen.findByTestId("extract-signature");
    expect(calls.previewExtract.mock.calls[0][1]).toMatchObject({ range, name: null });
    expect(calls.previewExtract.mock.calls[0][1]).not.toHaveProperty("node_ids");
  });

  it("набранное имя уходит в предпросмотр после паузы; подсказка, если имя занято", async () => {
    const user = userEvent.setup();
    const live = await import("@/mocks/fixtures/extract-preview.json");
    calls.preview.current = (request) => ({
      ...live.default,
      name: request.name === "collect" ? "collect2" : (request.name ?? live.default.name),
    });
    await renderDialog();
    const input = await screen.findByTestId("extract-name");
    await waitFor(() => expect(input).toHaveValue("addLine"));

    await user.clear(input);
    await user.type(input, "collect");
    await waitFor(() =>
      expect(calls.previewExtract.mock.calls.at(-1)?.[1]).toMatchObject({ name: "collect" }),
    );
    expect(await screen.findByTestId("extract-renamed")).toHaveTextContent("collect2");
  });

  it("пока имя не ушло в предпросмотр, «Выделить» закрыто", async () => {
    const user = userEvent.setup();
    await renderDialog();
    const input = await screen.findByTestId("extract-name");
    await waitFor(() => expect(screen.getByTestId("extract-apply")).toBeEnabled());
    await user.type(input, "X");
    expect(screen.getByTestId("extract-apply")).toBeDisabled();
    await waitFor(() =>
      expect(calls.previewExtract.mock.calls.at(-1)?.[1]).toMatchObject({ name: "addLineX" }),
    );
    await waitFor(() => expect(screen.getByTestId("extract-apply")).toBeEnabled());
  });

  it("если выделить нельзя, объясняет почему и не даёт применить", async () => {
    calls.preview.current = () => refused;
    await renderDialog();
    const dialog = await screen.findByTestId("extract-dialog");
    expect(await within(dialog).findByTestId("preconditions")).toHaveTextContent(
      "в выделенном фрагменте есть continue",
    );
    expect(within(dialog).getByTestId("extract-unavailable")).toHaveTextContent(
      ru.extractDialog.unavailable,
    );
    expect(within(dialog).getByTestId("extract-apply")).toBeDisabled();
  });

  it("применение: имя и блоки уходят на сервер, итог проверки показан, выбор сбрасывается", async () => {
    const user = userEvent.setup();
    const { workspace, runs } = await renderDialog();
    workspace.getState().selectNode(NODES[0], "graph");
    const input = await screen.findByTestId("extract-name");
    await waitFor(() => expect(input).toHaveValue("addLine"));
    await user.click(screen.getByTestId("extract-apply"));

    await waitFor(() => expect(calls.applyExtract).toHaveBeenCalledTimes(1));
    expect(calls.applyExtract.mock.calls[0].slice(0, 3)).toEqual([
      1,
      project.head_version_id,
      { node_ids: NODES, name: null },
    ]);
    await waitFor(() => expect(runs.getState().order).toEqual([RUN]));
    expect(runs.getState().entries[RUN].kind).toBe("gate");
    await waitFor(() => expect(emitters.has(RUN)).toBe(true));

    send(
      { event: "gate", data: { stage: "compile", status: "passed", details: null } },
      { event: "gate", data: { stage: "scenarios", status: "passed", details: null } },
      {
        event: "result",
        data: {
          summary: {
            passed: 11,
            total: 11,
            coverage: null,
            version_status: "passed",
            message: "11 из 11",
            data: { candidate: "promoted" },
          },
        },
      },
      { event: "status", data: { status: "done" } },
      { event: "done", data: {} },
    );
    const outcome = await screen.findByTestId("extract-outcome");
    await waitFor(() =>
      expect(within(outcome).getByTestId("extract-outcome-message")).toHaveTextContent(
        ru.extractDialog.outcome.promoted,
      ),
    );

    await user.click(screen.getByRole("button", { name: ru.extractDialog.close }));
    expect(workspace.getState().extractTarget).toBeNull();
    expect(workspace.getState().selection).toBeNull(); // блоки прежней версии после выделения уже другие
  });

  it("красный gate: версия ждёт решения, «Отклонить» оставляет код прежним", async () => {
    const user = userEvent.setup();
    await renderDialog();
    await waitFor(() => expect(screen.getByTestId("extract-apply")).toBeEnabled());
    await user.click(screen.getByTestId("extract-apply"));
    await waitFor(() => expect(emitters.has(RUN)).toBe(true));
    send(
      { event: "gate", data: { stage: "scenarios", status: "failed", details: null } },
      {
        event: "result",
        data: {
          summary: {
            passed: 10,
            total: 11,
            coverage: null,
            version_status: "failed",
            message: "10 из 11",
            data: { candidate: "candidate" },
          },
        },
      },
      { event: "status", data: { status: "done" } },
      { event: "done", data: {} },
    );
    expect(await screen.findByTestId("extract-accept")).toBeInTheDocument();
    expect(screen.getByTestId("extract-outcome")).toHaveTextContent(
      ru.extractDialog.outcome.candidate,
    );
    await user.click(screen.getByRole("button", { name: ru.candidate.reject }));
    await waitFor(() =>
      expect(screen.getByTestId("extract-outcome-message")).toHaveTextContent(
        ru.extractDialog.decided.rejected,
      ),
    );
  });
});
