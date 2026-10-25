import { loader, type Monaco } from "@monaco-editor/react";

import type { ColorMode } from "@/lib/hooks/use-color-mode";

// Monaco берётся из public/monaco (его туда копирует scripts/copy-monaco.mjs): без CDN работает офлайн.
loader.config({ paths: { vs: "/monaco/vs" } });

const EDITOR_THEMES: Record<ColorMode, string> = { dark: "jb-dark", light: "jb-light" };

/** Имя темы Monaco для темы интерфейса. */
export function editorThemeOf(mode: ColorMode): string {
  return EDITOR_THEMES[mode];
}

/** Тёмная и светлая темы редактора в тон интерфейса (docs/09 § 6). */
export function defineEditorTheme(monaco: Monaco): void {
  monaco.editor.defineTheme(EDITOR_THEMES.dark, {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "keyword", foreground: "b69cff" },
      { token: "string", foreground: "8bd5a5" },
      { token: "number", foreground: "f3b676" },
      { token: "comment", foreground: "7b82a8", fontStyle: "italic" },
      { token: "type.identifier", foreground: "7cc4f5" },
      { token: "annotation", foreground: "e4c76f" },
    ],
    colors: {
      "editor.background": "#14162a",
      "editor.foreground": "#e6e8f5",
      "editorLineNumber.foreground": "#8087bd",
      "editorLineNumber.activeForeground": "#aab0d8",
      "editor.lineHighlightBackground": "#1c1f38",
      "editor.selectionBackground": "#3b3f7a88",
      "editorCursor.foreground": "#b69cff",
      "editorGutter.background": "#14162a",
      "scrollbarSlider.background": "#3b3f7a55",
    },
  });
  monaco.editor.defineTheme(EDITOR_THEMES.light, {
    base: "vs",
    inherit: true,
    rules: [
      { token: "keyword", foreground: "6d3fd6" },
      { token: "string", foreground: "17693a" },
      { token: "number", foreground: "a24b00" },
      { token: "comment", foreground: "5f668c", fontStyle: "italic" },
      { token: "type.identifier", foreground: "0d5f9e" },
      { token: "annotation", foreground: "7a5f00" },
    ],
    colors: {
      "editor.background": "#ffffff",
      "editor.foreground": "#1b1d33",
      "editorLineNumber.foreground": "#6b7194",
      "editorLineNumber.activeForeground": "#2f3358",
      "editor.lineHighlightBackground": "#f3f4fb",
      "editor.selectionBackground": "#c9cdf588",
      "editorCursor.foreground": "#6d3fd6",
      "editorGutter.background": "#ffffff",
      "scrollbarSlider.background": "#b6bad855",
    },
  });
}
