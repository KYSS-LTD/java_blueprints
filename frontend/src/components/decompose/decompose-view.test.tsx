import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { DecomposePlan } from "@/lib/api/actions";
import type { RunEvent } from "@/lib/api/sse";
import { ru } from "@/lib/i18n/ru";
import fixture from "@/mocks/fixtures/decompose-plan.json";
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
  plan: vi.fn(),
  rescore: vi.fn(),
  apply: vi.fn(),
  rescoreImpl: { current: null as null | ((plan: unknown) => Promise<unknown>) },
}));
vi.mock("@/lib/api/actions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/actions")>();
  return {
    ...actual,
    planDecompose: (...args: Parameters<typeof actual.planDecompose>) => {
      calls.plan(...args);
      return actual.planDecompose(...args);
    },
    rescoreDecompose: (...args: Parameters<typeof actual.rescoreDecompose>) => {
      calls.rescore(...args);
      return calls.rescoreImpl.current
        ? calls.rescoreImpl.current(args[2])
        : actual.rescoreDecompose(...args);
    },
    applyDecompose: (...args: Parameters<typeof actual.applyDecompose>) => {
      calls.apply(...args);
      return actual.applyDecompose(...args);
    },
  };
});

const plan = fixture as DecomposePlan;
const core = plan.classes.find((cls) => cls.role === "core")!;
const clusters = plan.classes.filter((cls) => cls.role !== "core" && cls.role !== "model");
const [first, second] = clusters;
const memberOfFirst = first.members[0];
const lastPlan = () => calls.rescore.mock.calls.at(-1)![2] as DecomposePlan;
const classNamed = (p: DecomposePlan, name: string) => p.classes.find((cls) => cls.name === name)!;

const RUN = 8;
const send = (...events: RunEvent[]) =>
  act(() => {
    for (const event of events) emitters.get(RUN)?.(event);
  });

async function renderView() {
  const { DecomposeView } = await import("@/components/decompose/decompose-view");
  const { useRunStore } = await import("@/stores/run-store");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <DecomposeView projectId={1} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  await screen.findAllByTestId("decompose-class");
  return { runs: useRunStore };
}

const card = (name: string) =>
  screen.getAllByTestId("decompose-class").find((c) => c.getAttribute("data-class") === name)!;
const hasCard = (name: string) =>
  screen.queryAllByTestId("decompose-class").some((c) => c.getAttribute("data-class") === name);
const memberRow = (name: string, memberId: string) =>
  within(card(name))
    .getAllByTestId("decompose-member")
    .find((row) => row.getAttribute("data-member") === memberId)!;

/** Перенос мышью: jsdom не знает DataTransfer, поэтому подставляем заглушку. */
function dragMember(row: HTMLElement, onto: HTMLElement) {
  const dataTransfer = { setData: vi.fn(), effectAllowed: "", dropEffect: "" };
  fireEvent.dragStart(row, { dataTransfer });
  fireEvent.dragOver(onto, { dataTransfer });
  fireEvent.drop(onto, { dataTransfer });
  fireEvent.dragEnd(row, { dataTransfer });
}

describe("DecomposeView на моках", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_MOCK", "1");
    vi.resetModules();
    emitters.clear();
    calls.plan.mockClear();
    calls.rescore.mockClear();
    calls.apply.mockClear();
    calls.rescoreImpl.current = null;
  });
  afterEach(() => vi.unstubAllEnvs());

  it("слева «До» с метриками монолита, справа «После» с карточками классов", async () => {
    await renderView();
    const before = screen.getByTestId("decompose-before");
    expect(within(before).getByTestId("before-lines")).toHaveTextContent(
      String(plan.metrics.before.lines),
    );
    expect(within(before).getByTestId("before-lcom4")).toHaveTextContent("1.0");
    expect(within(before).getByText(core.name)).toBeInTheDocument();

    expect(screen.getByTestId("after-lines")).toHaveTextContent(String(plan.metrics.after.lines));
    expect(screen.getByTestId("after-lines")).toHaveTextContent("−57 %");
    expect(screen.getByTestId("after-classes")).toHaveTextContent(String(clusters.length));

    const names = screen.getAllByTestId("decompose-class").map((c) => c.getAttribute("data-class"));
    expect(names).toEqual([core.name, ...clusters.map((cls) => cls.name)]);
    expect(within(card(first.name)).getAllByTestId("decompose-member")).toHaveLength(
      first.members.length,
    );
    expect(within(card(first.name)).getByTestId("decompose-lines")).toHaveTextContent(
      String(first.lines),
    );
    expect(screen.getAllByTestId("decompose-link").length).toBe(plan.edges.length);
    expect(screen.getByTestId("decompose-detached")).toHaveTextContent("Order.java");
    expect(calls.plan.mock.calls[0][1]).toEqual({ max_classes: 6 });
  });

  it("закреплённые члены ядра заперты: их не перетащить и не перенести", async () => {
    const user = userEvent.setup();
    await renderView();
    const coreCard = card(core.name);
    const pinnedBox = within(coreCard).getByTestId("decompose-pinned");
    const rows = within(pinnedBox).getAllByTestId("decompose-member");
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) {
      expect(row).toHaveAttribute("data-movable", "false");
      expect(row).toHaveAttribute("draggable", "false");
      expect(within(row).queryByTestId("member-move")).not.toBeInTheDocument();
    }
    expect(
      within(first.members.length ? card(first.name) : coreCard).queryByTestId("decompose-pinned"),
    ).toBeNull();

    // попытка уронить закреплённый член в другой класс ничего не отправляет
    dragMember(rows[0], card(first.name));
    await user.click(document.body);
    expect(calls.rescore).not.toHaveBeenCalled();
  });

  it("перенос мышью: метод уходит в другую карточку, сервер пересчитывает план", async () => {
    await renderView();
    dragMember(memberRow(first.name, memberOfFirst), card(second.name));

    await waitFor(() => expect(calls.rescore).toHaveBeenCalledTimes(1));
    expect(calls.rescore.mock.calls[0][0]).toBe(1);
    expect(calls.rescore.mock.calls[0][1]).toBe(project.head_version_id);
    const sent = lastPlan();
    expect(classNamed(sent, second.name).members).toContain(memberOfFirst);
    expect(classNamed(sent, first.name).members).not.toContain(memberOfFirst);

    await waitFor(() =>
      expect(within(card(second.name)).getAllByTestId("decompose-member")).toHaveLength(
        second.members.length + 1,
      ),
    );
    expect(within(card(first.name)).getAllByTestId("decompose-member")).toHaveLength(
      first.members.length - 1,
    );
    expect(screen.getByTestId("decompose-reset")).toBeInTheDocument();
  });

  it("перенос из меню «Перенести в…» работает без мыши", async () => {
    const user = userEvent.setup();
    await renderView();
    const row = memberRow(first.name, memberOfFirst);
    await user.click(within(row).getByTestId("member-move"));
    const targets = await screen.findAllByTestId("member-move-target");
    expect(targets.map((t) => t.textContent)).toEqual(
      plan.classes
        .filter((c) => c.role !== "model" && c.name !== first.name)
        .map((c) => expect.stringContaining(c.name)),
    );
    await user.click(targets.find((t) => t.textContent?.includes(core.name))!);
    await waitFor(() => expect(calls.rescore).toHaveBeenCalled());
    expect(classNamed(lastPlan(), core.name).members).toContain(memberOfFirst);
  });

  it("метод, брошенный на «Новый класс», создаёт класс Helper", async () => {
    await renderView();
    const row = memberRow(first.name, memberOfFirst);
    const dataTransfer = { setData: vi.fn(), effectAllowed: "", dropEffect: "" };
    fireEvent.dragStart(row, { dataTransfer });
    const zone = screen.getByTestId("decompose-new-class");
    expect(zone).toHaveTextContent(ru.decompose.newClassHint);
    fireEvent.dragOver(zone, { dataTransfer });
    fireEvent.drop(zone, { dataTransfer });

    await waitFor(() => expect(calls.rescore).toHaveBeenCalled());
    const created = classNamed(lastPlan(), "Helper");
    expect(created).toMatchObject({ role: "util", file: "Helper.java", members: [memberOfFirst] });
    await waitFor(() => expect(hasCard("Helper")).toBe(true));
  });

  it("класс, оставшийся без методов, убирается из плана", async () => {
    const small = clusters.reduce((best, cls) =>
      cls.members.length < best.members.length ? cls : best,
    );
    await renderView();
    for (const id of small.members) {
      dragMember(memberRow(small.name, id), card(core.name));
      await waitFor(() =>
        expect(card(core.name).querySelector(`[data-member="${id}"]`)).not.toBeNull(),
      );
    }
    await waitFor(() => expect(hasCard(small.name)).toBe(false));
    expect(lastPlan().classes.some((cls) => cls.name === small.name)).toBe(false);
    expect(screen.getByTestId("after-classes")).toHaveTextContent(String(clusters.length - 1));
  });

  it("отказ сервера возвращает прежний план", async () => {
    calls.rescoreImpl.current = () =>
      Promise.reject(new Error("член нельзя переносить: вызывается из анонимного класса"));
    await renderView();
    dragMember(memberRow(first.name, memberOfFirst), card(second.name));
    await waitFor(() => expect(calls.rescore).toHaveBeenCalled());
    await waitFor(() =>
      expect(within(card(first.name)).getAllByTestId("decompose-member")).toHaveLength(
        first.members.length,
      ),
    );
    expect(within(card(second.name)).getAllByTestId("decompose-member")).toHaveLength(
      second.members.length,
    );
    expect(screen.queryByTestId("decompose-reset")).not.toBeInTheDocument();
  });

  it("переименование: имя и файл меняются, неверное и занятое имена не уходят на сервер", async () => {
    const user = userEvent.setup();
    await renderView();
    const input = within(card(first.name)).getByTestId("decompose-name");

    await user.clear(input);
    await user.type(input, "1bad");
    expect(within(card(first.name)).getByRole("alert")).toHaveTextContent(
      ru.decompose.renameInvalid,
    );
    await user.tab();
    expect(calls.rescore).not.toHaveBeenCalled();
    expect(within(card(first.name)).getByTestId("decompose-name")).toHaveValue(first.name);

    await user.clear(input);
    await user.type(input, second.name);
    expect(within(card(first.name)).getByRole("alert")).toHaveTextContent(ru.decompose.renameTaken);
    await user.tab();
    expect(calls.rescore).not.toHaveBeenCalled();

    await user.clear(input);
    await user.type(input, "OrderRules{Enter}");
    await waitFor(() => expect(calls.rescore).toHaveBeenCalledTimes(1));
    expect(classNamed(lastPlan(), "OrderRules")).toMatchObject({
      file: "OrderRules.java",
      members: first.members,
    });
    await waitFor(() => expect(hasCard("OrderRules")).toBe(true));
    expect(screen.getByText("OrderRules.java")).toBeInTheDocument();
  });

  it("ядро и вынесенные типы не переименовываются", async () => {
    await renderView();
    expect(within(card(core.name)).queryByTestId("decompose-name")).not.toBeInTheDocument();
    expect(screen.getAllByTestId("decompose-name")).toHaveLength(clusters.length);
  });

  it("«Сбросить правки» возвращает автоматический план", async () => {
    const user = userEvent.setup();
    await renderView();
    dragMember(memberRow(first.name, memberOfFirst), card(second.name));
    await user.click(await screen.findByTestId("decompose-reset"));
    await waitFor(() =>
      expect(within(card(first.name)).getAllByTestId("decompose-member")).toHaveLength(
        first.members.length,
      ),
    );
    expect(screen.queryByTestId("decompose-reset")).not.toBeInTheDocument();
  });

  it("число классов меняет параметры и строит план заново", async () => {
    const user = userEvent.setup();
    await renderView();
    await user.click(screen.getByRole("button", { name: ru.decompose.fewer }));
    expect(screen.getByTestId("decompose-max")).toHaveTextContent("5");
    await waitFor(() => expect(calls.plan).toHaveBeenCalledTimes(2));
    expect(calls.plan.mock.calls[1][1]).toEqual({ max_classes: 5 });
  });

  it("«Применить»: предпросмотр с файлами, затем применение плана и итог проверки", async () => {
    const user = userEvent.setup();
    const { runs } = await renderView();
    await user.click(screen.getByTestId("decompose-apply"));
    const dialog = await screen.findByTestId("decompose-dialog");
    const files = await within(dialog).findByTestId("decompose-files");
    for (const cls of clusters) expect(files).toHaveTextContent(cls.file);
    expect(within(dialog).getByTestId("file-diffs-stub")).toHaveTextContent(first.file);

    await user.click(within(dialog).getByTestId("decompose-confirm"));
    await waitFor(() => expect(calls.apply).toHaveBeenCalledTimes(1));
    expect(calls.apply.mock.calls[0][1]).toBe(project.head_version_id);
    expect(calls.apply.mock.calls[0][2]).toEqual(plan);
    await waitFor(() => expect(runs.getState().entries[RUN]?.kind).toBe("gate"));
    await waitFor(() => expect(emitters.has(RUN)).toBe(true));

    send(
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
    await waitFor(() =>
      expect(within(dialog).getByTestId("decompose-outcome-message")).toHaveTextContent(
        ru.decomposeDialog.outcome.promoted,
      ),
    );
  });

  it("правка плана попадает в применяемый план", async () => {
    const user = userEvent.setup();
    await renderView();
    dragMember(memberRow(first.name, memberOfFirst), card(second.name));
    await waitFor(() =>
      expect(within(card(second.name)).getAllByTestId("decompose-member")).toHaveLength(
        second.members.length + 1,
      ),
    );
    await user.click(screen.getByTestId("decompose-apply"));
    const dialog = await screen.findByTestId("decompose-dialog");
    await user.click(await within(dialog).findByTestId("decompose-confirm"));
    await waitFor(() => expect(calls.apply).toHaveBeenCalled());
    const applied = calls.apply.mock.calls[0][2] as DecomposePlan;
    expect(classNamed(applied, second.name).members).toContain(memberOfFirst);
  });

  it("предупреждения плана показываются списком", async () => {
    calls.rescoreImpl.current = (sent) =>
      Promise.resolve({
        ...(sent as DecomposePlan),
        warnings: ["циклическая зависимость между A и B"],
      });
    await renderView();
    expect(screen.queryByTestId("decompose-warnings")).not.toBeInTheDocument();
    dragMember(memberRow(first.name, memberOfFirst), card(second.name));
    expect(await screen.findByTestId("decompose-warnings")).toHaveTextContent(
      "циклическая зависимость между A и B",
    );
  });
});
