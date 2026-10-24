import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { ru } from "@/lib/i18n/ru";
import { initialRunState } from "@/lib/run/run-state";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/workspace/file-diffs", () => ({
  FileDiffs: ({
    diffs,
  }: {
    diffs: Array<{ path: string; before: string | null; after: string | null }>;
  }) => (
    <div data-testid="file-diffs-stub">
      {diffs.map((d) => `${d.path}: ${d.before} -> ${d.after}`).join("; ")}
    </div>
  ),
}));

async function renderPanel() {
  const { TestsPanel } = await import("@/components/workspace/tests-panel");
  const { useRunStore } = await import("@/stores/run-store");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <TestsPanel projectId={1} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return { store: useRunStore };
}

describe("TestsPanel на моках", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_MOCK", "1");
    vi.resetModules();
  });
  afterEach(() => vi.unstubAllEnvs());

  it("показывает сценарии проекта и состояние эталона", async () => {
    await renderPanel();
    const rows = await screen.findAllByTestId("test-row");
    expect(rows).toHaveLength(11);
    expect(within(rows[0]).getByText("bad-format")).toBeInTheDocument();
    expect(within(rows[0]).getByText(ru.tests.baselineState.recorded)).toBeInTheDocument();
  });

  it("результат прогона раскрашивает строки, а расхождение раскрывается в сравнение", async () => {
    const user = userEvent.setup();
    const { store } = await renderPanel();
    await screen.findAllByTestId("test-row");
    store.setState({
      order: [50],
      entries: {
        50: {
          id: 50,
          label: ru.tests.started.test,
          kind: "test",
          stdin: null,
          args: [],
          startedAt: 0,
          state: {
            ...initialRunState,
            status: "done",
            done: true,
            items: [
              {
                scenario_id: 1,
                name: "bad-format",
                verdict: "PASS",
                duration_ms: 12,
                baseline_ms: 10,
                diff: null,
              },
              {
                scenario_id: 2,
                name: "boundary-values",
                verdict: "FAIL",
                duration_ms: 15,
                baseline_ms: 11,
                diff: {
                  stdout: { expected: "11", actual: "-9", unified: "-11\n+-9" },
                  stderr: null,
                  exit_code: null,
                  exception: null,
                },
              },
            ],
            result: {
              passed: 1,
              total: 2,
              coverage: null,
              version_status: "failed",
              message: "Есть расхождения — 1 из 2",
              data: null,
            },
          },
        },
      },
    });

    expect(await screen.findByTestId("tests-message")).toHaveTextContent(
      "Есть расхождения — 1 из 2",
    );
    const rows = screen.getAllByTestId("test-row");
    expect(rows[0]).toHaveAttribute("data-verdict", "PASS");
    expect(rows[1]).toHaveAttribute("data-verdict", "FAIL");
    expect(within(rows[1]).getByText("вывод отличается")).toBeInTheDocument();

    await user.click(within(rows[1]).getByRole("button", { name: "boundary-values" }));
    expect(await screen.findByTestId("file-diffs-stub")).toHaveTextContent("Вывод: 11 -> -9");
    // сценарий, который прошёл, не раскрывается
    expect(within(rows[0]).getByRole("button", { name: "bad-format" })).toBeDisabled();
  });

  it("«Запустить тесты» создаёт запуск типа test", async () => {
    const user = userEvent.setup();
    const { store } = await renderPanel();
    await screen.findAllByTestId("test-row");
    await user.click(screen.getByTestId("run-tests"));
    await waitFor(() => expect(store.getState().order.length).toBe(1));
    const entry = store.getState().entries[store.getState().order[0]];
    expect(entry.kind).toBe("test");
    expect(entry.label).toBe(ru.tests.started.test);
  });

  it("форма сценария требует название", async () => {
    const user = userEvent.setup();
    await renderPanel();
    await screen.findAllByTestId("test-row");
    await user.click(screen.getByRole("button", { name: new RegExp(ru.tests.addScenario) }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: ru.scenarioDialog.save }));
    expect(await within(dialog).findByText(ru.scenarioDialog.nameRequired)).toBeInTheDocument();
  });
});
