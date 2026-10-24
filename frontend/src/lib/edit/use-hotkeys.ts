"use client";

import { useEffect } from "react";

/** Фокус в поле ввода или в редакторе кода: там Ctrl+Z и Delete принадлежат самому полю. */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return target.closest("input, textarea, select, [contenteditable=''], [contenteditable='true'], .monaco-editor") !== null;
}

type Handlers = {
  onUndo: () => void;
  onRedo: () => void;
};

/** Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y — отмена и повтор версий всего проекта (docs/09 § 3.3). */
export function useHistoryHotkeys({ onUndo, onRedo }: Handlers): void {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || isEditableTarget(event.target)) return;
      const key = event.key.toLowerCase();
      if (key !== "z" && key !== "y") return;
      event.preventDefault();
      if (key === "y" || event.shiftKey) onRedo();
      else onUndo();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onUndo, onRedo]);
}
