"use client";

import { useQuery } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";

import {
  type DecomposeOptions,
  type DecomposePlan,
  planDecompose,
  rescoreDecompose,
} from "@/lib/api/actions";
import { projectQuery } from "@/lib/api/queries";
import { ru } from "@/lib/i18n/ru";

import { sameComposition } from "./plan-edit";

const t = ru.decompose;

/**
 * План декомпозиции текущей версии и его ручные правки. Автоматический план строится один раз на версию
 * и набор параметров; каждая правка показывается сразу, а строки, связи и предупреждения приходят из
 * `decompose/rescore`. Отказ сервера (занятое имя, закреплённый член) возвращает прежний план.
 */
export function useDecomposePlan(projectId: number, options: DecomposeOptions) {
  const head = useQuery(projectQuery(projectId)).data?.head_version_id;
  const base = useQuery({
    queryKey: ["decompose", projectId, head, options],
    queryFn: () => planDecompose(projectId, options),
    enabled: head !== undefined,
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
  });
  const [state, setState] = useState<{ source: DecomposePlan; plan: DecomposePlan } | null>(null);
  const [recalculating, setRecalculating] = useState(false);
  const counter = useRef(0);

  // правки относятся к плану, из которого начались: новый автоматический план их отбрасывает
  const plan = state && state.source === base.data ? state.plan : (base.data ?? null);

  /** Принимает новый состав классов; `false`, если сервер его отклонил. */
  const commit = useCallback(
    async (next: DecomposePlan): Promise<boolean> => {
      const source = base.data;
      if (!source || head === undefined) return false;
      const previous = plan;
      const mine = ++counter.current;
      setState({ source, plan: next });
      setRecalculating(true);
      try {
        const scored = await rescoreDecompose(projectId, head, next);
        if (counter.current === mine) setState({ source, plan: scored });
        return true;
      } catch (error) {
        if (counter.current === mine) setState(previous ? { source, plan: previous } : null);
        toast.error(t.rescoreFailed, {
          description: error instanceof Error ? error.message : undefined,
        });
        return false;
      } finally {
        if (counter.current === mine) setRecalculating(false);
      }
    },
    [base.data, head, plan, projectId],
  );

  const reset = useCallback(() => {
    counter.current += 1;
    setState(null);
    setRecalculating(false);
  }, []);

  return {
    head,
    plan,
    isLoading: base.isLoading || head === undefined,
    error: base.error,
    recalculating,
    edited: plan !== null && base.data !== undefined && !sameComposition(plan, base.data),
    commit,
    reset,
  };
}
