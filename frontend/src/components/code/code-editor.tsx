"use client";

import Editor, { type Monaco, type OnChange, type OnMount } from "@monaco-editor/react";
import type { editor as MonacoEditor } from "monaco-editor";
import { useEffect, useRef } from "react";

import { minimalChange } from "@/lib/edit/text-edit";
import type { SourceRange } from "@/lib/graph/flow-model";

import { defineEditorTheme, EDITOR_THEME } from "./monaco-setup";

export type EditorMarker = {
  severity: "error" | "warning" | "info";
  message: string;
  range: SourceRange;
};

export type CodeEditorProps = {
  path: string;
  /** Текст, который должен быть в редакторе; если он ушёл от набранного, вносится минимальная правка. */
  value: string;
  readOnly?: boolean;
  /** Диапазон выбранного узла: подсвечивается всегда, прокручивается при смене `revealSeq`. */
  highlight: SourceRange | null;
  /** Номер выбора, к которому нужно прокрутить; `null` — только подсветить (выбор сделан в самом коде). */
  revealSeq: number | null;
  markers: EditorMarker[];
  /** Курсор поставлен пользователем (мышь или клавиатура). */
  onCursor?: (line: number, column: number) => void;
  /** Пользователь изменил текст (правки, внесённые снаружи через `value`, сюда не приходят). */
  onChange?: (text: string) => void;
  /** Ctrl+S. */
  onSave?: () => void;
};

/** Сколько подсвечивается строка, изменённая снаружи (операцией над графом, отменой): docs/09 § 3.3. */
const CHANGE_FLASH_MS = 1500;

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
  readOnly = false,
  highlight,
  revealSeq,
  markers,
  onCursor,
  onChange,
  onSave,
}: CodeEditorProps) {
  const editorRef = useRef<MonacoEditor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<Monaco | null>(null);
  const decorations = useRef<MonacoEditor.IEditorDecorationsCollection | null>(null);
  const flash = useRef<MonacoEditor.IEditorDecorationsCollection | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const cursorHandler = useRef(onCursor);
  const changeHandler = useRef(onChange);
  const saveHandler = useRef(onSave);
  const revealed = useRef(-1);
  // Правка, внесённая из `value`, не должна вернуться наверх как ввод пользователя.
  const applying = useRef(false);
  // Последний текст, сообщённый наверх: пока `value` не догнал набранное, отставший `value` не откатывает ввод.
  const emitted = useRef<string | null>(null);

  useEffect(() => {
    cursorHandler.current = onCursor;
    changeHandler.current = onChange;
    saveHandler.current = onSave;
  }, [onCursor, onChange, onSave]);
  useEffect(() => () => clearTimeout(flashTimer.current), []);

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

  // Текст пришёл снаружи (операция над графом, отмена, перечитанный файл): вносим только разницу,
  // чтобы курсор и прокрутка остались на месте, и коротко подсвечиваем изменённые строки.
  const syncValue = () => {
    const monaco = monacoRef.current;
    const editor = editorRef.current;
    const model = editor?.getModel();
    if (!monaco || !editor || !model) return;
    const current = model.getValue();
    if (value === current) {
      emitted.current = null;
      return;
    }
    if (emitted.current !== null && current === emitted.current) return;
    const change = minimalChange(current, value);
    if (!change) return;
    const from = model.getPositionAt(change.start);
    const to = model.getPositionAt(change.end);
    applying.current = true;
    try {
      // applyEdits не попадает в историю Monaco: Ctrl+Z в редакторе отменяет только набранное пользователем
      model.applyEdits([{ range: new monaco.Range(from.lineNumber, from.column, to.lineNumber, to.column), text: change.text }]);
    } finally {
      applying.current = false;
    }
    const start = model.getPositionAt(change.start);
    const end = model.getPositionAt(change.start + change.text.length);
    flash.current?.set([
      {
        range: new monaco.Range(start.lineNumber, 1, Math.max(end.lineNumber, start.lineNumber), 1),
        options: { isWholeLine: true, className: "jb-changed-flash" },
      },
    ]);
    clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => flash.current?.clear(), CHANGE_FLASH_MS);
  };

  // Эффекты читают актуальные пропсы: Monaco-объекты живут в ref'ах.
  useEffect(syncValue, [value, path]);
  useEffect(applyHighlight, [highlight, revealSeq, value, path]);
  useEffect(applyMarkers, [markers, value, path]);

  const onMount: OnMount = (editor, monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
    decorations.current = editor.createDecorationsCollection();
    flash.current = editor.createDecorationsCollection();
    editor.onDidChangeCursorPosition((event) => {
      if (event.source === "mouse" || event.source === "keyboard") {
        cursorHandler.current?.(event.position.lineNumber, event.position.column);
      }
    });
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => saveHandler.current?.());
    syncValue();
    applyHighlight();
    applyMarkers();
  };

  const handleChange: OnChange = (text) => {
    if (applying.current || text === undefined) return;
    emitted.current = text;
    changeHandler.current?.(text);
  };

  return (
    <Editor
      path={path}
      language="java"
      defaultValue={value}
      theme={EDITOR_THEME}
      beforeMount={defineEditorTheme}
      onMount={onMount}
      onChange={handleChange}
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
