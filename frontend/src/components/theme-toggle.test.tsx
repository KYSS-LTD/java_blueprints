import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "next-themes";
import { beforeEach, describe, expect, it } from "vitest";

import { ThemeToggle } from "@/components/theme-toggle";
import { ru } from "@/lib/i18n/ru";

function renderToggle() {
  render(
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false}>
      <ThemeToggle />
    </ThemeProvider>,
  );
}

describe("ThemeToggle", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.className = "";
  });

  it("по умолчанию тема тёмная, кнопка предлагает светлую", async () => {
    renderToggle();
    expect(await screen.findByRole("button", { name: ru.theme.toLight })).toBeInTheDocument();
    expect(document.documentElement).toHaveClass("dark");
  });

  it("переключает класс на <html>, подпись кнопки и запоминает выбор", async () => {
    const user = userEvent.setup();
    renderToggle();
    await user.click(await screen.findByRole("button", { name: ru.theme.toLight }));

    expect(await screen.findByRole("button", { name: ru.theme.toDark })).toBeInTheDocument();
    expect(document.documentElement).not.toHaveClass("dark");
    expect(window.localStorage.getItem("theme")).toBe("light");

    await user.click(screen.getByRole("button", { name: ru.theme.toDark }));
    expect(await screen.findByRole("button", { name: ru.theme.toLight })).toBeInTheDocument();
    expect(document.documentElement).toHaveClass("dark");
  });

  it("берёт сохранённую светлую тему", async () => {
    window.localStorage.setItem("theme", "light");
    renderToggle();
    expect(await screen.findByRole("button", { name: ru.theme.toDark })).toBeInTheDocument();
    expect(document.documentElement).not.toHaveClass("dark");
  });
});
