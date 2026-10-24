"use client";

import { useSyncExternalStore } from "react";

const subscribe = () => () => {};

/** `false` при серверной отрисовке и гидратации, `true` в браузере после неё. */
export function useIsClient(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
}
