import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ru } from "@/lib/i18n/ru";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

async function renderScreen() {
  const { ProjectsScreen } = await import("@/components/projects/projects-screen");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ProjectsScreen />
    </QueryClientProvider>,
  );
}

describe("ProjectsScreen на моках", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_MOCK", "1");
    vi.resetModules();
  });
  afterEach(() => vi.unstubAllEnvs());

  it("показывает проекты со ссылкой в рабочую область", async () => {
    await renderScreen();
    const link = await screen.findByRole("link", { name: "demo-orders" });
    expect(link).toHaveAttribute("href", "/projects/1");
    expect(screen.getByText(ru.preview.mockBadge)).toBeInTheDocument();
  });

  it("«Новый проект» открывает диалог импорта со вкладками", async () => {
    const user = userEvent.setup();
    await renderScreen();
    await user.click(screen.getAllByRole("button", { name: new RegExp(ru.home.newProject) })[0]);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("tab", { name: ru.importDialog.tabs.files })).toBeInTheDocument();
    expect(within(dialog).getByRole("tab", { name: ru.importDialog.tabs.sample })).toBeInTheDocument();
  });
});

describe("ProjectsScreen без backend", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("пустой список подсказывает, как начать", async () => {
    vi.resetModules();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ projects: [] })),
    );
    await renderScreen();
    expect(await screen.findByText(ru.home.empty)).toBeInTheDocument();
  });

  it("ошибка API показывается с подсказкой", async () => {
    vi.resetModules();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ error: { code: "INTERNAL", message: "База недоступна" } }, { status: 500 })),
    );
    await renderScreen();
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("База недоступна")).toBeInTheDocument();
  });
});
