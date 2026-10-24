"use client";

import { DiffEditor } from "@monaco-editor/react";

import { defineEditorTheme, EDITOR_THEME } from "./monaco-setup";

export type DiffViewProps = {
  path: string;
  before: string;
  after: string;
  /** Язык подсветки: Java для исходников, `plaintext` для вывода программы. */
  language?: string;
};

/** Сравнение двух текстов файла рядом (Monaco DiffEditor, только чтение). */
export default function DiffView({ path, before, after, language = "java" }: DiffViewProps) {
  return (
    <DiffEditor
      original={before}
      modified={after}
      originalModelPath={`diff-before/${path}`}
      modifiedModelPath={`diff-after/${path}`}
      language={language}
      theme={EDITOR_THEME}
      beforeMount={defineEditorTheme}
      loading={null}
      options={{
        readOnly: true,
        renderSideBySide: true,
        minimap: { enabled: false },
        fontFamily: "'JetBrains Mono Variable', ui-monospace, Consolas, monospace",
        fontSize: 12.5,
        scrollBeyondLastLine: false,
        automaticLayout: true,
        originalEditable: false,
        fixedOverflowWidgets: true,
      }}
    />
  );
}
