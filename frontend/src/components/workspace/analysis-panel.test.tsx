import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { RunEvent } from "@/lib/api/sse";
import { ru } from "@/lib/i18n/ru";

// Потоки событий запусков: тест сам решает, что и когда пришло.
const emitters = new Map<number, (event: RunEvent) => void>();
vi.mock("@/lib/api/sse", () => ({
  openRunEvents: (runId: number, onEvent: (event: RunEvent) => void) => {
    emitters.set(runId, onEvent);
    return () => {};
  },
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/workspace/file-diffs", () => ({
  FileDiffs: ({ diffs }: { diffs: Array<{ path: string; after: string | null }> }) => (
    <div data-testid="file-diffs-stub">{diffs.map((d) => `${d.path}: ${d.after}`).join("; ")}</div>
  ),
}));
vi.mock("@/components/workspace/growth-chart", () => ({
  GrowthChart: ({ samples, compared }: { samples?: unknown[]; compared?: unknown[] }) => (
    <div data-testid="growth-chart-stub">{(compared ?? samples ?? []).length} точек</div>
  ),
}));

const RUN = 8;

function send(...events: RunEvent[]) {
  act(() => {
    for (const event of events) emitters.get(RUN)?.(event);
  });
}

const summary = (data: Record<string, unknown> | null, patch: Record<string, unknown> = {}) => ({
  passed: null,
  total: null,
  coverage: null,
  version_status: null,
  message: null,
  data,
  ...patch,
});

async function renderPanel() {
  const { AnalysisPanel } = await import("@/components/workspace/analysis-panel");
  const { useRunStore } = await import("@/stores/run-store");
  const { useWorkspaceStore } = await import("@/stores/workspace-store");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <AnalysisPanel projectId={1} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  await screen.findAllByTestId("finding-card");
  return { runs: useRunStore, workspace: useWorkspaceStore };
}

describe("AnalysisPanel на моках", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_MOCK", "1");
    vi.resetModules();
    emitters.clear();
  });
  afterEach(() => vi.unstubAllEnvs());

  it("показывает находки, число рекомендаций и сложность программы", async () => {
    await renderPanel();
    expect(screen.getAllByTestId("finding-card")).toHaveLength(7);
    expect(screen.getByRole("tab", { name: `${ru.analysis.problems} (7)` })).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: `${ru.analysis.recommendations} (5)` }),
    ).toBeInTheDocument();
    expect(screen.getByText(ru.analysis.afterFix)).toBeInTheDocument();
  });

  it("фильтры по важности и категории сужают список и счётчики вкладок", async () => {
    const user = userEvent.setup();
    await renderPanel();
    const filters = screen.getByTestId("finding-filters");

    await user.click(within(filters).getByRole("button", { name: /Высокая/ }));
    expect(screen.getAllByTestId("finding-card")).toHaveLength(4);

    await user.click(within(filters).getByRole("button", { name: /Ошибки/ }));
    expect(screen.getAllByTestId("finding-card")).toHaveLength(3);
    expect(screen.getByRole("tab", { name: `${ru.analysis.problems} (3)` })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Высокая/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("«Почему» раскрывает дефект, риск, рекомендацию и «Проверить»", async () => {
    const user = userEvent.setup();
    await renderPanel();
    const card = screen.getAllByTestId("finding-card")[0];
    expect(within(card).queryByTestId("finding-details")).not.toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: ru.analysis.why }));
    const details = within(card).getByTestId("finding-details");
    expect(details).toHaveTextContent(ru.analysis.defect);
    expect(details).toHaveTextContent(ru.analysis.risk);
    expect(within(details).getByTestId("verify-button")).toBeInTheDocument();
  });

  it("клик по находке выбирает её узел и открывает метод", async () => {
    const user = userEvent.setup();
    const { workspace } = await renderPanel();
    const card = screen.getAllByTestId("finding-card")[0];
    await user.click(within(card).getByText(/Вложенный цикл/));
    const state = workspace.getState();
    expect(state.selection?.source).toBe("findings");
    expect(state.activeTabId).toBe("Main.java#Main.mergeDuplicates()");
  });

  it("«Проверить» запускает проверку и показывает показатель роста", async () => {
    const user = userEvent.setup();
    const { runs } = await renderPanel();
    const card = screen.getAllByTestId("finding-card")[0];
    await user.click(within(card).getByRole("button", { name: ru.analysis.why }));
    await user.click(within(card).getByTestId("verify-button"));

    await waitFor(() => expect(runs.getState().order).toEqual([RUN]));
    expect(runs.getState().entries[RUN].kind).toBe("verify");
    expect(within(card).getByTestId("verify-button")).toBeDisabled();

    send(
      { event: "status", data: { status: "running" } },
      {
        event: "result",
        data: {
          summary: summary(
            {
              samples: [
                { size: 1000, time_ms: 10, hits: 1_000_000 },
                { size: 2000, time_ms: 40, hits: 4_000_000 },
              ],
              growth_k: 1.97,
              predicted: "O(n·m)",
              warnings: ["время растёт быстрее счётчиков"],
            },
            { message: "k ≈ 1.97 → ~O(n²)" },
          ),
        },
      },
      { event: "status", data: { status: "done" } },
      { event: "done", data: {} },
    );

    const result = await within(card).findByTestId("verify-result");
    expect(result).toHaveTextContent("k ≈ 1.97 → ~O(n²)");
    expect(result).toHaveTextContent("1.97");
    expect(result).toHaveTextContent("O(n·m)");
    expect(result).toHaveTextContent("время растёт быстрее счётчиков");
    expect(within(result).getByTestId("growth-chart-stub")).toHaveTextContent("2 точек");
  });

  it("у находки BUG «Проверить» сообщает, выполняется ли место в сценариях", async () => {
    const user = userEvent.setup();
    await renderPanel();
    const card = screen
      .getAllByTestId("finding-card")
      .find((c) => c.getAttribute("data-rule") === "BUG-01") as HTMLElement;
    await user.click(within(card).getByRole("button", { name: ru.analysis.why }));
    await user.click(within(card).getByTestId("verify-button"));
    await waitFor(() => expect(emitters.has(RUN)).toBe(true));
    send(
      {
        event: "result",
        data: {
          summary: summary(
            { reached_by: 3, scenarios: 11 },
            { message: "Место находки выполняется в 3 из 11 сценариев" },
          ),
        },
      },
      { event: "status", data: { status: "done" } },
      { event: "done", data: {} },
    );
    expect(await within(card).findByTestId("verify-result")).toHaveTextContent("3 из 11");
  });

  it("недоступное исправление объясняет, какое предусловие не выполнено", async () => {
    await renderPanel();
    const card = screen
      .getAllByTestId("finding-card")
      .find((c) => c.getAttribute("data-rule") === "MAINT-01") as HTMLElement;
    const row = within(card).getByTestId("fix-row");
    expect(row).toHaveAttribute("data-available", "false");
    expect(row).toHaveTextContent(ru.analysis.fixUnavailable);
    expect(within(row).queryByTestId("fix-preview")).not.toBeInTheDocument();
  });

  it("длинный метод предлагает выделить фрагмент, а монолит — декомпозицию", async () => {
    const user = userEvent.setup();
    const { workspace } = await renderPanel();
    const byRule = (rule: string) =>
      screen.getAllByTestId("finding-card").find((c) => c.getAttribute("data-rule") === rule) as HTMLElement;

    await user.click(within(byRule("MAINT-01")).getByTestId("refactor-extract"));
    // метод открыт во вкладке: оттуда выбираются блоки для выделения
    expect(workspace.getState().tabs.map((t) => t.kind)).toEqual(["method"]);
    expect(workspace.getState().extractTarget).toBeNull(); // фрагмент выбирает пользователь

    await user.click(within(byRule("MAINT-05")).getByTestId("refactor-decompose"));
    expect(workspace.getState().activeTabId).toBe("decompose");
    expect(workspace.getState().tabs.map((t) => t.kind)).toEqual(["method", "decompose"]);

    // у находок без такого рефакторинга кнопок нет
    expect(within(byRule("PERF-01")).queryByTestId("refactor-row")).not.toBeInTheDocument();
  });

  it("вкладка «Рекомендации» показывает только исправимые находки с предусловиями", async () => {
    const user = userEvent.setup();
    const { workspace } = await renderPanel();
    await user.click(screen.getByRole("tab", { name: `${ru.analysis.recommendations} (5)` }));
    expect(workspace.getState().rightTab).toBe("recommendations");
    const cards = await screen.findAllByTestId("finding-card");
    expect(cards.every((c) => within(c).queryByTestId("fix-preview") !== null)).toBe(true);
    expect(within(cards[0]).getByTestId("preconditions")).toBeInTheDocument();
  });

  it("вкладка «Метрики» показывает таблицу и самые сложные методы", async () => {
    const user = userEvent.setup();
    await renderPanel();
    await user.click(screen.getByRole("tab", { name: ru.analysis.metrics }));
    const tab = await screen.findByTestId("metrics-tab");
    expect(within(tab).getByTestId("metric-codeLines")).toHaveTextContent("867");
    expect(within(tab).getByTestId("metric-cyclomaticMax")).toHaveTextContent("22");
    expect(within(tab).getByText("processCommand")).toBeInTheDocument();
  });
});

describe("исправление из панели «Анализ»", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_MOCK", "1");
    vi.resetModules();
    emitters.clear();
  });
  afterEach(() => vi.unstubAllEnvs());

  async function openFix(rule: string) {
    const user = userEvent.setup();
    const handles = await renderPanel();
    const card = screen
      .getAllByTestId("finding-card")
      .find((c) => c.getAttribute("data-rule") === rule) as HTMLElement;
    await user.click(within(card).getByTestId("fix-preview"));
    const dialog = await screen.findByTestId("fix-dialog");
    await within(dialog).findByTestId("file-diffs-stub");
    return { user, dialog, ...handles };
  }

  it("предпросмотр показывает эффект, предусловия и diff", async () => {
    const { dialog } = await openFix("PERF-01");
    expect(within(dialog).getByTestId("fix-effect")).toHaveTextContent("O(n·m)");
    expect(within(dialog).getByTestId("fix-effect")).toHaveTextContent("O(n)");
    expect(within(dialog).getByTestId("preconditions")).toHaveTextContent(
      "Тело зависит только от пары",
    );
    expect(within(dialog).getByTestId("file-diffs-stub")).toHaveTextContent("Main.java");
    expect(within(dialog).getByText(ru.fixDialog.behaviorSafe)).toBeInTheDocument();
  });

  it("«Применить» запускает проверку; зелёная — исправление стало текущей версией", async () => {
    const { user, dialog, runs } = await openFix("PERF-01");
    await user.click(within(dialog).getByTestId("fix-apply"));
    await waitFor(() => expect(runs.getState().order).toEqual([RUN]));
    expect(runs.getState().entries[RUN].kind).toBe("gate");

    send(
      { event: "status", data: { status: "running" } },
      { event: "gate", data: { stage: "syntax", status: "passed", details: null } },
      { event: "gate", data: { stage: "compile", status: "passed", details: null } },
      { event: "gate", data: { stage: "scenarios", status: "passed", details: null } },
      {
        event: "result",
        data: {
          summary: summary(
            { candidate: "promoted" },
            { passed: 11, total: 11, version_status: "passed", message: "11 из 11" },
          ),
        },
      },
      { event: "status", data: { status: "done" } },
      { event: "done", data: {} },
    );

    const outcome = within(dialog).getByTestId("fix-outcome");
    await waitFor(() =>
      expect(within(outcome).getByTestId("fix-outcome-message")).toHaveAttribute(
        "data-candidate",
        "promoted",
      ),
    );
    expect(outcome).toHaveTextContent(ru.fixDialog.outcome.promoted);
    expect(within(outcome).getByLabelText(ru.tests.started.gate)).toHaveTextContent(
      "Сценарии: пройден",
    );
  });

  it("красная проверка отклоняет версию и называет изменившиеся сценарии", async () => {
    const { user, dialog } = await openFix("PERF-01");
    await user.click(within(dialog).getByTestId("fix-apply"));
    await waitFor(() => expect(emitters.has(RUN)).toBe(true));
    send(
      { event: "gate", data: { stage: "scenarios", status: "failed", details: null } },
      {
        event: "item",
        data: {
          scenario_id: 2,
          name: "boundary-values",
          verdict: "FAIL",
          duration_ms: 15,
          baseline_ms: 11,
          diff: {
            stdout: { expected: "11", actual: "-9", unified: "" },
            stderr: null,
            exit_code: null,
            exception: null,
          },
        },
      },
      {
        event: "result",
        data: { summary: summary({ candidate: "rejected" }, { version_status: "failed" }) },
      },
      { event: "status", data: { status: "done" } },
      { event: "done", data: {} },
    );
    const outcome = within(dialog).getByTestId("fix-outcome");
    await waitFor(() => expect(outcome).toHaveTextContent(ru.fixDialog.outcome.rejected));
    expect(outcome).toHaveTextContent("boundary-values");
    expect(outcome).toHaveTextContent("вывод отличается");
    expect(within(dialog).queryByTestId("fix-accept")).not.toBeInTheDocument();
  });

  it("исправление, меняющее поведение, ждёт решения: принять или отклонить", async () => {
    const { user, dialog } = await openFix("BUG-01");
    expect(within(dialog).getByText(ru.fixDialog.behaviorChanges)).toBeInTheDocument();
    await user.click(within(dialog).getByTestId("fix-apply"));
    await waitFor(() => expect(emitters.has(RUN)).toBe(true));
    send(
      {
        event: "result",
        data: { summary: summary({ candidate: "candidate" }, { version_status: "failed" }) },
      },
      { event: "status", data: { status: "done" } },
      { event: "done", data: {} },
    );
    await waitFor(() => expect(within(dialog).getByTestId("fix-accept")).toBeInTheDocument());
    expect(within(dialog).getByTestId("fix-outcome")).toHaveTextContent(
      ru.fixDialog.outcome.candidate,
    );
    expect(within(dialog).getByRole("button", { name: ru.fixDialog.reject })).toBeInTheDocument();
  });

  async function waitForCandidate(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) {
    await user.click(within(dialog).getByTestId("fix-apply"));
    await waitFor(() => expect(emitters.has(RUN)).toBe(true));
    send(
      {
        event: "result",
        data: { summary: summary({ candidate: "candidate" }, { version_status: "failed" }) },
      },
      { event: "status", data: { status: "done" } },
      { event: "done", data: {} },
    );
    await waitFor(() => expect(within(dialog).getByTestId("fix-accept")).toBeInTheDocument());
  }

  it("принятое исправление показывает решение и предлагает записать эталон заново", async () => {
    const { user, dialog, workspace } = await openFix("BUG-01");
    await waitForCandidate(user, dialog);
    await user.click(within(dialog).getByTestId("fix-accept"));

    const message = within(dialog).getByTestId("fix-outcome-message");
    await waitFor(() => expect(message).toHaveAttribute("data-decision", "accepted"));
    expect(message).toHaveTextContent(ru.fixDialog.decided.accepted);
    expect(within(dialog).queryByTestId("fix-accept")).not.toBeInTheDocument();

    await user.click(within(dialog).getByTestId("fix-rebaseline"));
    // моки отвечают на любой запуск номером 8, поэтому проверяем действие, а не новый запуск
    await waitFor(() => expect(workspace.getState().bottomTab).toBe("tests"));
    await waitFor(() => expect(screen.queryByTestId("fix-dialog")).not.toBeInTheDocument());
  });

  it("отклонённое исправление оставляет код прежним", async () => {
    const { user, dialog } = await openFix("BUG-01");
    await waitForCandidate(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: ru.fixDialog.reject }));
    const message = within(dialog).getByTestId("fix-outcome-message");
    await waitFor(() => expect(message).toHaveAttribute("data-decision", "rejected"));
    expect(message).toHaveTextContent(ru.fixDialog.decided.rejected);
    expect(within(dialog).queryByTestId("fix-rebaseline")).not.toBeInTheDocument();
  });

  it("пока идёт проверка, редактор кода закрыт для правок", async () => {
    const { user, dialog } = await openFix("PERF-01");
    const { useEditorStore } = await import("@/stores/editor-store");
    expect(useEditorStore.getState().busy).toBe(0);
    await user.click(within(dialog).getByTestId("fix-apply"));
    await waitFor(() => expect(useEditorStore.getState().busy).toBe(1));
    send(
      { event: "result", data: { summary: summary({ candidate: "promoted" }) } },
      { event: "status", data: { status: "done" } },
      { event: "done", data: {} },
    );
    await waitFor(() => expect(useEditorStore.getState().busy).toBe(0));
  });
});
