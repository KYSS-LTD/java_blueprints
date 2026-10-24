"use client";

import { DiffEditor, type DiffOnMount } from "@monaco-editor/react";
import { useEffect, useRef } from "react";

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
  const editor = useRef<Parameters<DiffOnMount>[0] | null>(null);

  // Обёртка при размонтировании сначала удаляет модели, а потом редактор, и Monaco бросает
  // «TextModel got disposed before DiffEditorWidget model got reset». Её очистка запускается после
  // нашей (родитель раньше потомка), поэтому модель отцепляем от редактора здесь.
  useEffect(
    () => () => {
      editor.current?.setModel(null);
      editor.current = null;
    },
    [],
  );

  return (
    <DiffEditor
      onMount={(mounted) => {
        editor.current = mounted;
      }}
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
        // в файле на тысячу строк меняются единицы: остальное свёрнуто, видны только правки с контекстом
        hideUnchangedRegions: { enabled: true, contextLineCount: 3, minimumLineCount: 4 },
        fixedOverflowWidgets: true,
      }}
    />
  );
}
