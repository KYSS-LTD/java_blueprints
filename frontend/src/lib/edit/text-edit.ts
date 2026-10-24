export type TextChange = {
  /** Смещение начала замены в старом тексте (UTF-16). */
  start: number;
  /** Смещение конца замены в старом тексте. */
  end: number;
  text: string;
};

/**
 * Минимальная замена, превращающая `before` в `after`: общие начало и конец остаются на месте.
 * Редактор применяет её вместо замены всего текста, поэтому курсор, прокрутка и выделение вне
 * правки не сбиваются. `null` — тексты одинаковы.
 */
export function minimalChange(before: string, after: string): TextChange | null {
  if (before === after) return null;
  const limit = Math.min(before.length, after.length);
  let head = 0;
  while (head < limit && before.charCodeAt(head) === after.charCodeAt(head)) head += 1;
  let tail = 0;
  while (
    tail < limit - head &&
    before.charCodeAt(before.length - 1 - tail) === after.charCodeAt(after.length - 1 - tail)
  ) {
    tail += 1;
  }
  // не разрываем суррогатную пару: правка начинается и заканчивается на границе символа
  if (head > 0 && isLowSurrogate(before.charCodeAt(head)) && isHighSurrogate(before.charCodeAt(head - 1))) head -= 1;
  if (tail > 0 && isHighSurrogate(before.charCodeAt(before.length - tail - 1)) && isLowSurrogate(before.charCodeAt(before.length - tail))) {
    tail -= 1;
  }
  return { start: head, end: before.length - tail, text: after.slice(head, after.length - tail) };
}

function isHighSurrogate(code: number): boolean {
  return code >= 0xd800 && code <= 0xdbff;
}

function isLowSurrogate(code: number): boolean {
  return code >= 0xdc00 && code <= 0xdfff;
}
