package jbtrace;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;

/**
 * Счётчики выполнения для трассировки (docs/07 § 6). Инструментированная копия программы вызывает
 * {@link #hit} перед операторами и {@link #cond} вокруг условий; при завершении JVM счётчики
 * записываются в файл из свойства {@code jb.trace.out} (JSON без внешних зависимостей).
 *
 * После {@code jb.trace.limit} вызовов (по умолчанию 5 млн) счётчики перестают расти: так
 * трассировка не замедляет программу, которая крутит цикл миллионы раз, а в отчёте стоит
 * признак {@code saturated}.
 */
public final class JbTrace {
    private static final long LIMIT = Long.getLong("jb.trace.limit", 5_000_000L);
    private static long[] counts = new long[1024];
    private static long total;
    private static boolean saturated;

    static {
        final String out = System.getProperty("jb.trace.out");
        if (out != null && !out.isEmpty()) {
            Runtime.getRuntime().addShutdownHook(new Thread(() -> dump(out), "jb-trace-dump"));
        }
    }

    private JbTrace() {
    }

    /** Оператор или блок начал выполняться. */
    public static void hit(int id) {
        if (total >= LIMIT) {
            saturated = true;
            return;
        }
        total++;
        if (id >= counts.length) {
            grow(id);
        }
        counts[id]++;
    }

    /**
     * Условие вычислено: слот {@code id} считает вычисления, слот {@code id + 1} — истинные.
     * Значение возвращается без изменений, поэтому порядок вычислений программы не меняется.
     */
    public static boolean cond(int id, boolean value) {
        hit(id);
        if (value) {
            hit(id + 1);
        }
        return value;
    }

    private static void grow(int id) {
        int size = counts.length;
        while (size <= id) {
            size *= 2;
        }
        counts = java.util.Arrays.copyOf(counts, size);
    }

    private static synchronized void dump(String path) {
        final StringBuilder json = new StringBuilder(256);
        json.append("{\"saturated\":").append(saturated).append(",\"total\":").append(total)
            .append(",\"counts\":{");
        boolean first = true;
        for (int i = 0; i < counts.length; i++) {
            if (counts[i] == 0) {
                continue;
            }
            if (!first) {
                json.append(',');
            }
            first = false;
            json.append('"').append(i).append("\":").append(counts[i]);
        }
        json.append("}}");
        try {
            Files.write(Paths.get(path), json.toString().getBytes(StandardCharsets.UTF_8));
        } catch (IOException | RuntimeException ignored) {
            // трассировка оценочная: сбой записи не должен менять код выхода программы
        }
    }
}
