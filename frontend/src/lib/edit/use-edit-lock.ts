"use client";

import { useEffect } from "react";

import { useEditorStore } from "@/stores/editor-store";

/**
 * Пока на сервере идёт запуск, который сам меняет HEAD (исправление, оптимизация), код в редакторе
 * доступен только для чтения: набранный в это время текст иначе перезаписал бы результат запуска.
 */
export function useEditLock(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const editor = useEditorStore.getState();
    editor.beginBusy();
    return () => editor.endBusy();
  }, [active]);
}
