"use client";

import Editor, { type Monaco, type OnMount } from "@monaco-editor/react";
import type { editor as MonacoEditor } from "monaco-editor";
import { useEffect, useRef } from "react";

import type { SourceRange } from "@/lib/graph/flow-model";

import { defineEditorTheme, EDITOR_THEME } from "./monaco-setup";

export type EditorMarker = {
  severity: "error" | "warning" | "info";
  message: string;
  range: SourceRange;
};

export type CodeEditorProps = {
  path: string;
  value: string;
  readOnly?: boolean;
  /** Диапазон выбранного узла: подсвечивается всегда, прокручивается при смене `revealSeq`. */
  highlight: SourceRange | null;
  /** Номер выбора, к которому нужно прокрутить; `null` — только подсветить (выбор сделан в самом коде). */
  revealSeq: number | null;
  markers: EditorMarker[];
  /** Курсор поставлен пользователем (мышь или клавиатура). */
  onCursor?: (line: number, column: number) => void;
};

function toRange(monaco: Monaco, range: SourceRange) {
  return new monaco.Range(range.start_line, range.start_col, range.end_line, range.end_col);
}

const SEVERITY = (monaco: Monaco, severity: EditorMarker["severity"]) =>
  severity === "error"
    ? monaco.MarkerSeverity.Error
    : severity === "warning"
      ? monaco.MarkerSeverity.Warning
      : monaco.MarkerSeverity.Info;

/** Monaco: Java, подсветка узла графа, маркеры ошибок, прокрутка к узлу (docs/09 § 3.5, § 4). */
export default function CodeEditor({
  path,
  value,
  readOnly = true,
  highlight,
  revealSeq,
  markers,
  onCursor,
}: CodeEditorProps) {
  const editorRef = useRef<MonacoEditor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<Monaco | null>(null);
  const decorations = useRef<MonacoEditor.IEditorDecorationsCollection | null>(null);
  const cursorHandler = useRef(onCursor);
  const revealed = useRef(-1);

  useEffect(() => {
    cursorHandler.current = onCursor;
  }, [onCursor]);

  const applyHighlight = () => {
    const monaco = monacoRef.current;
    const editor = editorRef.current;
    if (!monaco || !editor) return;
    const model = editor.getModel();
    const valid = highlight !== null && model !== null && highlight.end_line <= model.getLineCount();
    decorations.current?.set(
      valid && highlight
        ? [
            {
              range: toRange(monaco, highlight),
              options: { className: "jb-node-highlight", overviewRuler: undefined },
            },
          ]
        : [],
    );
    if (valid && highlight && revealSeq !== null && revealed.current !== revealSeq) {
      revealed.current = revealSeq;
      editor.revealRangeInCenterIfOutsideViewport(toRange(monaco, highlight), monaco.editor.ScrollType.Smooth);
    }
  };

  const applyMarkers = () => {
    const monaco = monacoRef.current;
    const model = editorRef.current?.getModel();
    if (!monaco || !model) return;
    monaco.editor.setModelMarkers(
      model,
      "jb",
      markers
        .filter((m) => m.range.start_line <= model.getLineCount())
        .map((m) => ({
          severity: SEVERITY(monaco, m.severity),
          message: m.message,
          startLineNumber: m.range.start_line,
          startColumn: m.range.start_col,
          endLineNumber: m.range.end_line,
          endColumn: Math.max(m.range.end_col, m.range.start_col + 1),
        })),
    );
  };

  // Эффекты без зависимостей по замыканиям: Monaco-объекты живут в ref'ах, поэтому читаем актуальные пропсы здесь.
  useEffect(applyHighlight, [highlight, revealSeq, value, path]);
  useEffect(applyMarkers, [markers, value, path]);

  const onMount: OnMount = (editor, monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
    decorations.current = editor.createDecorationsCollection();
    editor.onDidChangeCursorPosition((event) => {
      if (event.source === "mouse" || event.source === "keyboard") {
        cursorHandler.current?.(event.position.lineNumber, event.position.column);
      }
    });
    applyHighlight();
    applyMarkers();
  };

  return (
    <Editor
      path={path}
      language="java"
      value={value}
      theme={EDITOR_THEME}
      beforeMount={defineEditorTheme}
      onMount={onMount}
      loading={null}
      options={{
        readOnly,
        domReadOnly: readOnly,
        minimap: { enabled: false },
        fontFamily: "'JetBrains Mono Variable', ui-monospace, Consolas, monospace",
        fontSize: 12.5,
        lineHeight: 19,
        scrollBeyondLastLine: false,
        automaticLayout: true,
        renderLineHighlight: "line",
        smoothScrolling: true,
        padding: { top: 8, bottom: 8 },
        stickyScroll: { enabled: false },
        fixedOverflowWidgets: true,
      }}
    />
  );
}
