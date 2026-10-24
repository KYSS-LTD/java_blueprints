import { loader, type Monaco } from "@monaco-editor/react";

// Monaco берётся из public/monaco (его туда копирует scripts/copy-monaco.mjs): без CDN работает офлайн.
loader.config({ paths: { vs: "/monaco/vs" } });

export const EDITOR_THEME = "jb-dark";

/** Тёмная тема в тон интерфейса (docs/09 § 6). */
export function defineEditorTheme(monaco: Monaco): void {
  monaco.editor.defineTheme(EDITOR_THEME, {
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
      "editorLineNumber.foreground": "#5b6290",
      "editorLineNumber.activeForeground": "#aab0d8",
      "editor.lineHighlightBackground": "#1c1f38",
      "editor.selectionBackground": "#3b3f7a88",
      "editorCursor.foreground": "#b69cff",
      "editorGutter.background": "#14162a",
      "scrollbarSlider.background": "#3b3f7a55",
    },
  });
}
