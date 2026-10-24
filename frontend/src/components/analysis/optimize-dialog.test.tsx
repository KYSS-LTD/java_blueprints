import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { OptimizePlan } from "@/lib/api/actions";
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
vi.mock("@/components/workspace/growth-chart", () => ({
  GrowthChart: ({ compared }: { compared?: unknown[] }) => (
    <div data-testid="growth-chart-stub">{compared?.length ?? 0} точек</div>
  ),
}));

const calls = vi.hoisted(() => ({
  applyOptimize: vi.fn(),
  moveHead: vi.fn(),
  plan: { current: null as null | OptimizePlan },
}));
vi.mock("@/lib/api/actions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/actions")>();
  return {
    ...actual,
    planOptimize: (...args: Parameters<typeof actual.planOptimize>) =>
      calls.plan.current ? Promise.resolve(calls.plan.current) : actual.planOptimize(...args),
    applyOptimize: (...args: Parameters<typeof actual.applyOptimize>) => {
      calls.applyOptimize(...args);
      return actual.applyOptimize(...args);
    },
  };
});
vi.mock("@/lib/edit/session", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/edit/session")>();
  return { ...actual, moveHead: calls.moveHead };
});

const RUN = 8;
const send = (...events: RunEvent[]) =>
  act(() => {
    for (const event of events) emitters.get(RUN)?.(event);
  });

async function renderDialog() {
  const { OptimizeDialog } = await import("@/components/analysis/optimize-dialog");
  const { useRunStore } = await import("@/stores/run-store");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onOpenChange = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <OptimizeDialog projectId={1} open onOpenChange={onOpenChange} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return { runs: useRunStore, onOpenChange };
}

const measured = {
  speedup: 5.2,
  size: 8000,
  basis: "time",
  before_k: 1.98,
  after_k: 1.02,
  samples: [
    {
      size: 4000,
      before: { size: 4000, time_ms: 80, hits: 16_000_000 },
      after: { size: 4000, time_ms: 20, hits: 4_000 },
    },
    {
      size: 8000,
      before: { size: 8000, time_ms: 320, hits: 64_000_000 },
      after: { size: 8000, time_ms: 40, hits: 8_000 },
    },
  ],
};

describe("OptimizeDialog на моках", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_MOCK", "1");
    vi.resetModules();
    emitters.clear();
    calls.applyOptimize.mockClear();
    calls.moveHead.mockReset();
    calls.plan.current = null;
  });
  afterEach(() => vi.unstubAllEnvs());

  it("показывает план: безопасные шаги с галочками и итоговую сложность", async () => {
    await renderDialog();
    const steps = await screen.findAllByTestId("optimize-step");
    expect(steps).toHaveLength(3);
    expect(steps[0]).toHaveTextContent("OPT-01");
    expect(screen.getByTestId("optimize-effect")).toHaveTextContent("O(n²)");
    expect(screen.getByTestId("optimize-effect")).toHaveTextContent("O(n)");
    for (const step of steps) expect(within(step).getByRole("checkbox")).toBeChecked();
    expect(screen.getByTestId("optimize-start")).toBeEnabled();
  });

  it("без безопасных шагов объясняет, что делать дальше", async () => {
    calls.plan.current = {
      version_id: 3,
      steps: [],
      effect: { complexity_before: "O(n)", complexity_after: null, notes: null },
    };
    await renderDialog();
    expect(await screen.findByTestId("optimize-empty")).toHaveTextContent(ru.optimizeDialog.empty);
    expect(screen.getByTestId("optimize-start")).toBeDisabled();
  });

  it("запуск отправляет только отмеченные шаги и ведёт по их статусам", async () => {
    const user = userEvent.setup();
    const { runs } = await renderDialog();
    const steps = await screen.findAllByTestId("optimize-step");
    await user.click(within(steps[1]).getByRole("checkbox"));
    expect(screen.getByTestId("optimize-start")).toBeEnabled();

    await user.click(screen.getByTestId("optimize-start"));
    await waitFor(() => expect(calls.applyOptimize).toHaveBeenCalledTimes(1));
    const [projectId, base, ids] = calls.applyOptimize.mock.calls[0];
    expect(projectId).toBe(1);
    expect(base).toBe(project.head_version_id);
    expect(ids).toHaveLength(2);
    expect(ids[0]).toContain("PERF-01");
    expect(ids[1]).toContain("PERF-03");
    await waitFor(() => expect(runs.getState().entries[RUN]?.kind).toBe("optimize"));

    const progress = await screen.findByTestId("optimize-progress");
    const rows = within(progress).getAllByTestId("optimize-run-step");
    expect(rows).toHaveLength(2);
    expect(rows.every((r) => r.getAttribute("data-status") === "pending")).toBe(true);

    send(
      { event: "status", data: { status: "running" } },
      {
        event: "step",
        data: { step_id: ids[0], title: "Индексация", status: "applying", reason: null },
      },
    );
    expect(within(progress).getAllByTestId("optimize-run-step")[0]).toHaveAttribute(
      "data-status",
      "applying",
    );

    send(
      {
        event: "step",
        data: { step_id: ids[0], title: "Индексация", status: "passed", reason: null },
      },
      { event: "progress", data: { done: 1, total: 2 } },
      {
        event: "step",
        data: {
          step_id: ids[1],
          title: "StringBuilder",
          status: "skipped",
          reason: "предусловия не выполнены",
        },
      },
    );
    const after = within(progress).getAllByTestId("optimize-run-step");
    expect(after[0]).toHaveAttribute("data-status", "passed");
    expect(after[1]).toHaveAttribute("data-status", "skipped");
    expect(after[1]).toHaveTextContent("предусловия не выполнены");
  });

  it("итог: применённые шаги, измеренное ускорение, график и откат всей пачки", async () => {
    const user = userEvent.setup();
    await renderDialog();
    await screen.findAllByTestId("optimize-step");
    await user.click(screen.getByTestId("optimize-start"));
    await waitFor(() => expect(calls.applyOptimize).toHaveBeenCalled());
    const ids = calls.applyOptimize.mock.calls[0][2] as string[];
    await waitFor(() => expect(emitters.has(RUN)).toBe(true));

    send(
      {
        event: "result",
        data: {
          summary: {
            passed: 2,
            total: 3,
            coverage: null,
            version_status: "passed",
            message: "Применено шагов: 2 из 3; ускорение до 5.2× (N=8000)",
            data: {
              measured,
              applied: ids.slice(0, 2),
              skipped: { [ids[2]]: "находка пропала после предыдущих шагов" },
              head_version_id: 5,
              complexity_after: "O(n)",
            },
          },
        },
      },
      { event: "status", data: { status: "done" } },
      { event: "done", data: {} },
    );

    const report = await screen.findByTestId("optimize-report");
    expect(report).toHaveTextContent("Применено шагов: 2 из 3");
    expect(report).toHaveTextContent(ru.optimizeDialog.behaviorPassed);
    expect(report).toHaveTextContent("до 5.2×");
    expect(report).toHaveTextContent("находка пропала после предыдущих шагов");
    expect(within(report).getByTestId("growth-chart-stub")).toHaveTextContent("2 точек");

    calls.moveHead.mockResolvedValue({ head_version_id: project.head_version_id });
    await user.click(screen.getByTestId("optimize-rollback"));
    await waitFor(() => expect(calls.moveHead).toHaveBeenCalledTimes(1));
    expect(calls.moveHead.mock.calls[0][1]).toBe(1);
    expect(calls.moveHead.mock.calls[0][2]).toEqual({ restore: project.head_version_id });
    await waitFor(() => expect(screen.queryByTestId("optimize-rollback")).not.toBeInTheDocument());
  });

  it("если ничего не применено, отката нет, а ускорение «не измерено» с причиной", async () => {
    const user = userEvent.setup();
    await renderDialog();
    await screen.findAllByTestId("optimize-step");
    await user.click(screen.getByTestId("optimize-start"));
    await waitFor(() => expect(emitters.has(RUN)).toBe(true));
    send(
      {
        event: "result",
        data: {
          summary: {
            passed: 0,
            total: 3,
            coverage: null,
            version_status: "unknown",
            message: "Применено шагов: 0 из 3",
            data: {
              measured: { error: "ничего не применено" },
              applied: [],
              skipped: {},
              head_version_id: 3,
            },
          },
        },
      },
      { event: "status", data: { status: "done" } },
      { event: "done", data: {} },
    );
    const report = await screen.findByTestId("optimize-report");
    expect(within(report).getByTestId("speedup")).toHaveAttribute("data-measured", "false");
    expect(report).toHaveTextContent("ничего не применено");
    expect(screen.queryByTestId("optimize-rollback")).not.toBeInTheDocument();
  });

  it("пока идёт оптимизация, код в редакторе закрыт для правок", async () => {
    const user = userEvent.setup();
    await renderDialog();
    const { useEditorStore } = await import("@/stores/editor-store");
    await screen.findAllByTestId("optimize-step");
    await user.click(screen.getByTestId("optimize-start"));
    await waitFor(() => expect(useEditorStore.getState().busy).toBe(1));
    await waitFor(() => expect(emitters.has(RUN)).toBe(true));
    send({ event: "status", data: { status: "done" } }, { event: "done", data: {} });
    await waitFor(() => expect(useEditorStore.getState().busy).toBe(0));
  });
});
