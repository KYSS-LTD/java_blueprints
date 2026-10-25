import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.EnumMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Scanner;
import java.util.stream.Collectors;

/**
 * Библиотека: каталог изданий, читатели, выдачи, возвраты, штрафы и отчёты.
 *
 * Программа читает команды из стандартного ввода (одна команда в строке) и печатает результат.
 * Весь код намеренно собран в одном классе: разбор ввода, проверка, учёт выдач, расчёт штрафов,
 * отчёты и вывод. Вспомогательные типы (издания, читатели, выдачи) лежат в этом же файле.
 */
public class Main {
    static final int MAX_LOANS = 3;
    static final int LOAN_DAYS = 14;
    static final int RENEW_DAYS = 7;
    static final int MAX_RENEWALS = 2;
    static final double FINE_PER_DAY = 5.0;
    static final double FINE_LIMIT = 500.0;
    static final int MAX_TITLE_LENGTH = 40;
    static final int MAX_NAME_LENGTH = 30;
    static final String SEPARATOR = "----------------------------------------";
    static final String UNKNOWN_AUTHOR = "без автора";

    private final List<Item> items = new ArrayList<>();
    private final List<Reader> readers = new ArrayList<>();
    private final List<Loan> loans = new ArrayList<>();
    private final List<String> journal = new ArrayList<>();
    private final List<Fine> fines = new ArrayList<>();
    private final Stats stats = new Stats();
    private int today = 1;
    private int commandCount = 0;
    private int errorCount = 0;
    private int nextItemId = 1;
    private int nextReaderId = 1;

    /** Счётчики работы библиотеки: отдельный вложенный тип внутри монолита. */
    static class Stats {
        int lent;
        int returned;
        int renewed;
        double finesCharged;
        double finesPaid;

        double balance() {
            return finesCharged - finesPaid;
        }
    }

    public static void main(String[] args) {
        Scanner in = new Scanner(System.in, StandardCharsets.UTF_8);
        Main app = new Main();
        app.printHeader();
        app.run(in);
        app.printFooter();
    }

    // ------------------------------------------------------------------
    // Основной цикл и разбор команд
    // ------------------------------------------------------------------

    void run(Scanner in) {
        while (in.hasNextLine()) {
            String line = in.nextLine().trim();
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            commandCount++;
            journal.add(line);
            try {
                boolean goOn = processCommand(line, in);
                if (!goOn) {
                    break;
                }
            } catch (LibraryException e) {
                errorCount++;
                System.out.println("Ошибка библиотеки: " + e.getMessage());
            } catch (InputException e) {
                errorCount++;
                System.out.println("Ошибка ввода: " + e.getMessage());
            }
        }
    }

    private boolean processCommand(String line, Scanner in) {
        String[] parts = line.split("\\s+");
        String command = parts[0].toLowerCase();
        switch (command) {
            case "book":
                addBook(parts);
                break;
            case "magazine":
                addMagazine(parts);
                break;
            case "reader":
                addReader(parts);
                break;
            case "batch": {
                int count = parseInt(requireArg(parts, 1, "количество строк"), "количество строк");
                int accepted = readBatch(in, count);
                System.out.printf("Принято изданий: %d из %d%n", accepted, count);
                break;
            }
            case "lend":
                lend(parseInt(requireArg(parts, 1, "номер читателя"), "номер читателя"),
                        parseInt(requireArg(parts, 2, "номер издания"), "номер издания"));
                break;
            case "return":
                giveBack(parseInt(requireArg(parts, 1, "номер читателя"), "номер читателя"),
                        parseInt(requireArg(parts, 2, "номер издания"), "номер издания"));
                break;
            case "renew":
                renew(parseInt(requireArg(parts, 1, "номер читателя"), "номер читателя"),
                        parseInt(requireArg(parts, 2, "номер издания"), "номер издания"));
                break;
            case "day":
                advanceDay(parseInt(requireArg(parts, 1, "число дней"), "число дней"));
                break;
            case "pay":
                pay(parseInt(requireArg(parts, 1, "номер читателя"), "номер читателя"),
                        parseMoney(requireArg(parts, 2, "сумма"), "сумма"));
                break;
            case "find":
                printItems(findByTitle(requireArg(parts, 1, "часть названия")));
                break;
            case "author":
                printItems(findByAuthor(requireArg(parts, 1, "автор")));
                break;
            case "list":
                printItems(items);
                break;
            case "readers":
                printReaders();
                break;
            case "overdue":
                printOverdue();
                break;
            case "authors":
                System.out.println("Авторы: " + String.join(", ", uniqueAuthors()));
                break;
            case "top":
                printTopReaders(parseInt(requireArg(parts, 1, "количество"), "количество"));
                break;
            case "merge":
                System.out.println("Объединено изданий: " + mergeDuplicateItems());
                break;
            case "csv":
                System.out.print(buildCatalogCsv());
                break;
            case "genres":
                printGenres();
                break;
            case "report":
                printReport();
                break;
            case "history":
                printHistory();
                break;
            case "help":
                printHelp();
                break;
            case "boom":
                throw new IllegalStateException("Принудительное завершение по команде boom");
            case "quit":
            case "exit":
                return false;
            default:
                System.out.println("Неизвестная команда: " + command);
        }
        return true;
    }

    private int readBatch(Scanner in, int count) {
        int accepted = 0;
        for (int i = 0; i < count && in.hasNextLine(); i++) {
            String row = in.nextLine().trim();
            try {
                String[] parts = parseBatchRow(row);
                addBook(new String[] {"book", parts[0], parts[1], parts[2], parts[3], parts[4]});
                accepted++;
            } catch (ImportException e) {
                errorCount++;
                System.out.println("Пропущена строка: " + e.getMessage());
            } catch (InputException e) {
                errorCount++;
                System.out.println("Пропущена строка: " + e.getMessage());
            } catch (LibraryException e) {
                errorCount++;
                System.out.println("Пропущена строка: " + e.getMessage());
            }
        }
        return accepted;
    }

    private String[] parseBatchRow(String row) throws ImportException {
        String[] parts = row.split("\\s+");
        if (parts.length < 5) {
            throw new ImportException("строка пакета неполная: «" + row + "»");
        }
        return parts;
    }

    // ------------------------------------------------------------------
    // Разбор аргументов
    // ------------------------------------------------------------------

    private String requireArg(String[] parts, int index, String what) {
        if (parts.length <= index) {
            throw new InputException("не указан параметр: " + what);
        }
        return parts[index];
    }

    private int parseInt(String text, String what) {
        try {
            return Integer.parseInt(text);
        } catch (NumberFormatException e) {
            throw new InputException("«" + text + "» не число (" + what + ")");
        }
    }

    private double parseMoney(String text, String what) {
        try {
            return Double.parseDouble(text.replace(',', '.'));
        } catch (NumberFormatException e) {
            throw new InputException("«" + text + "» не сумма (" + what + ")");
        }
    }

    private Genre parseGenre(String text) {
        switch (text.toLowerCase()) {
            case "роман":
                return Genre.FICTION;
            case "наука":
                return Genre.SCIENCE;
            case "история":
                return Genre.HISTORY;
            case "детям":
                return Genre.KIDS;
            default:
                throw new InputException("неизвестный жанр: «" + text + "»");
        }
    }

    // ------------------------------------------------------------------
    // Проверка полей: три похожих метода
    // ------------------------------------------------------------------

    private void checkTitle(String title) {
        if (title == null || title.isBlank()) {
            throw new InputException("пустое название");
        }
        if (title.length() > MAX_TITLE_LENGTH) {
            throw new InputException("название длиннее " + MAX_TITLE_LENGTH + " символов");
        }
        for (int i = 0; i < title.length(); i++) {
            char c = title.charAt(i);
            if (Character.isISOControl(c)) {
                throw new InputException("в названии управляющий символ");
            }
        }
    }

    private void checkAuthor(String author) {
        if (author == null || author.isBlank()) {
            throw new InputException("пустой автор");
        }
        if (author.length() > MAX_NAME_LENGTH) {
            throw new InputException("автор длиннее " + MAX_NAME_LENGTH + " символов");
        }
        for (int i = 0; i < author.length(); i++) {
            char c = author.charAt(i);
            if (Character.isISOControl(c)) {
                throw new InputException("в имени автора управляющий символ");
            }
        }
    }

    private void checkReaderName(String name) {
        if (name == null || name.isBlank()) {
            throw new InputException("пустое имя читателя");
        }
        if (name.length() > MAX_NAME_LENGTH) {
            throw new InputException("имя длиннее " + MAX_NAME_LENGTH + " символов");
        }
        for (int i = 0; i < name.length(); i++) {
            char c = name.charAt(i);
            if (Character.isISOControl(c)) {
                throw new InputException("в имени читателя управляющий символ");
            }
        }
    }

    // ------------------------------------------------------------------
    // Каталог
    // ------------------------------------------------------------------

    private void addBook(String[] parts) {
        String title = requireArg(parts, 1, "название").replace('_', ' ');
        String author = requireArg(parts, 2, "автор").replace('_', ' ');
        Genre genre = parseGenre(requireArg(parts, 3, "жанр"));
        int copies = parseInt(requireArg(parts, 4, "число экземпляров"), "число экземпляров");
        checkTitle(title);
        checkAuthor(author);
        if (copies < 1 || copies > 50) {
            throw new LibraryException("число экземпляров должно быть от 1 до 50");
        }
        int pages = 200;
        if (parts.length > 5) {
            try {
                pages = Integer.parseInt(parts[5]);
            } catch (NumberFormatException e) {
            }
        }
        Book book = new Book(nextItemId++, title, genre, copies, author, pages);
        items.add(book);
        System.out.printf("Добавлена книга №%d: %s, %s (%s), %d экз.%n", book.getId(), book.getTitle(),
                book.getAuthor(), genre.getLabel(), copies);
    }

    private void addMagazine(String[] parts) {
        String title = requireArg(parts, 1, "название").replace('_', ' ');
        int issue = parseInt(requireArg(parts, 2, "номер выпуска"), "номер выпуска");
        int copies = parseInt(requireArg(parts, 3, "число экземпляров"), "число экземпляров");
        checkTitle(title);
        if (issue < 1) {
            throw new LibraryException("номер выпуска должен быть положительным");
        }
        if (copies < 1 || copies > 50) {
            throw new LibraryException("число экземпляров должно быть от 1 до 50");
        }
        Magazine magazine = new Magazine(nextItemId++, title, Genre.PERIODICAL, copies, issue);
        items.add(magazine);
        System.out.printf("Добавлен журнал №%d: %s, выпуск %d, %d экз.%n", magazine.getId(),
                magazine.getTitle(), issue, copies);
    }

    private void addReader(String[] parts) {
        String name = requireArg(parts, 1, "имя").replace('_', ' ');
        checkReaderName(name);
        Reader reader = new Reader(nextReaderId++, name);
        readers.add(reader);
        System.out.printf("Читатель №%d: %s%n", reader.getId(), reader.getName());
    }

    private Item findItemById(int id) {
        for (Item item : items) {
            if (item.getId() == id) {
                return item;
            }
        }
        return null;
    }

    private Reader findReaderById(int id) {
        for (Reader reader : readers) {
            if (reader.getId() == id) {
                return reader;
            }
        }
        return null;
    }

    private Item requireItem(int id) {
        Item item = findItemById(id);
        if (item == null) {
            throw new LibraryException("издание №" + id + " не найдено");
        }
        return item;
    }

    private Reader requireReader(int id) {
        Reader reader = findReaderById(id);
        if (reader == null) {
            throw new LibraryException("читатель №" + id + " не найден");
        }
        return reader;
    }

    private List<Item> findByTitle(String part) {
        List<Item> found = new ArrayList<>();
        String needle = part.toLowerCase();
        for (Item item : items) {
            if (item.getTitle().toLowerCase().contains(needle)) {
                found.add(item);
            }
        }
        return found;
    }

    private List<Item> findByAuthor(String author) {
        List<Item> found = new ArrayList<>();
        for (Item item : items) {
            if (item instanceof Book && ((Book) item).getAuthor().equalsIgnoreCase(author.replace('_', ' '))) {
                found.add(item);
            }
        }
        return found;
    }

    // ------------------------------------------------------------------
    // Выдача и возврат
    // ------------------------------------------------------------------

    private void lend(int readerId, int itemId) {
        Reader reader = requireReader(readerId);
        Item item = requireItem(itemId);
        if (item.getAvailable() < 1) {
            throw new LibraryException("все экземпляры «" + item.getTitle() + "» на руках");
        }
        if (countActiveLoans(readerId) >= MAX_LOANS) {
            throw new LibraryException("у читателя " + reader.getName() + " уже " + MAX_LOANS + " издания");
        }
        if (fineOf(readerId) > FINE_LIMIT) {
            throw new LibraryException("штраф читателя " + reader.getName() + " превышает " + FINE_LIMIT);
        }
        for (Loan loan : loans) {
            if (loan.getReaderId() == readerId && loan.getItemId() == itemId && !loan.isReturned()) {
                throw new LibraryException("читатель уже взял «" + item.getTitle() + "»");
            }
        }
        int days = item.loanDays();
        Loan loan = new Loan(readerId, itemId, today, today + days);
        loans.add(loan);
        item.take();
        stats.lent++;
        System.out.printf("Выдано: %s -> %s, вернуть до дня %d%n", item.getTitle(), reader.getName(), loan.getDue());
    }

    private void giveBack(int readerId, int itemId) {
        Reader reader = requireReader(readerId);
        Item item = requireItem(itemId);
        Loan found = null;
        for (Loan loan : loans) {
            if (loan.getReaderId() == readerId && loan.getItemId() == itemId && !loan.isReturned()) {
                found = loan;
                break;
            }
        }
        if (found == null) {
            throw new LibraryException("выдача не найдена: " + reader.getName() + ", «" + item.getTitle() + "»");
        }
        found.markReturned(today);
        item.putBack();
        stats.returned++;
        int late = found.lateDays(today);
        if (late > 0) {
            double fine = late * FINE_PER_DAY;
            reader.addFine(fine);
            fines.add(new Fine(readerId, fine, today));
            stats.finesCharged += fine;
            System.out.printf("Возврат с опозданием на %d дн.: штраф %.2f%n", late, fine);
        } else {
            System.out.printf("Возврат: %s <- %s%n", item.getTitle(), reader.getName());
        }
    }

    private void renew(int readerId, int itemId) {
        Reader reader = requireReader(readerId);
        Item item = requireItem(itemId);
        for (Loan loan : loans) {
            if (loan.getReaderId() == readerId && loan.getItemId() == itemId && !loan.isReturned()) {
                if (loan.getRenewals() >= MAX_RENEWALS) {
                    throw new LibraryException("продлений больше нет: «" + item.getTitle() + "»");
                }
                if (loan.lateDays(today) > 0) {
                    throw new LibraryException("просроченную выдачу нельзя продлить");
                }
                loan.extend(RENEW_DAYS);
                stats.renewed++;
                System.out.printf("Продлено: %s, новый срок — день %d%n", item.getTitle(), loan.getDue());
                return;
            }
        }
        throw new LibraryException("выдача не найдена: " + reader.getName() + ", «" + item.getTitle() + "»");
    }

    private void advanceDay(int days) {
        if (days < 1 || days > 365) {
            throw new LibraryException("число дней должно быть от 1 до 365");
        }
        today += days;
        System.out.println("Сегодня день " + today);
    }

    private void pay(int readerId, double amount) {
        Reader reader = requireReader(readerId);
        if (amount <= 0) {
            throw new LibraryException("сумма должна быть положительной");
        }
        double due = reader.getFine();
        if (amount > due) {
            throw new LibraryException("штраф читателя " + reader.getName() + " — " + format(due) + ", больше платить нельзя");
        }
        reader.addFine(-amount);
        stats.finesPaid += amount;
        System.out.printf("Оплачено %s, остаток штрафа %s%n", format(amount), format(reader.getFine()));
    }

    private int countActiveLoans(int readerId) {
        int count = 0;
        for (Loan loan : loans) {
            if (loan.getReaderId() == readerId && !loan.isReturned()) {
                count++;
            }
        }
        return count;
    }

    private double fineOf(int readerId) {
        Reader reader = findReaderById(readerId);
        return reader == null ? 0 : reader.getFine();
    }

    // ------------------------------------------------------------------
    // Преобразования данных
    // ------------------------------------------------------------------

    int mergeDuplicateItems() {
        List<Item> merged = new ArrayList<>();
        for (Item candidate : items) {
            boolean found = false;
            for (Item existing : merged) {
                if (existing.getTitle().equals(candidate.getTitle())
                        && existing.getGenre() == candidate.getGenre()
                        && existing.kind().equals(candidate.kind())) {
                    existing.addCopies(candidate.getCopies());
                    found = true;
                    break;
                }
            }
            if (!found) {
                merged.add(candidate.copy());
            }
        }
        int removed = items.size() - merged.size();
        items.clear();
        items.addAll(merged);
        return removed;
    }

    List<String> uniqueAuthors() {
        List<String> authors = new ArrayList<>();
        for (Item item : items) {
            String author = item instanceof Book ? ((Book) item).getAuthor() : UNKNOWN_AUTHOR;
            if (!authors.contains(author)) {
                authors.add(author);
            }
        }
        Collections.sort(authors);
        return authors;
    }

    String buildCatalogCsv() {
        String csv = "номер;вид;название;жанр;экземпляров;на руках\n";
        for (Item item : items) {
            csv += item.getId() + ";" + item.kind() + ";" + item.getTitle() + ";"
                    + item.getGenre().getLabel() + ";" + item.getCopies() + ";"
                    + (item.getCopies() - item.getAvailable()) + "\n";
        }
        return csv;
    }

    boolean sameTitle(Item a, Item b) {
        return a.getTitle() == b.getTitle();
    }

    double averageFine() {
        double total = 0;
        int count = 0;
        for (Reader reader : readers) {
            if (reader.getFine() > 0) {
                total += reader.getFine();
                count++;
            }
        }
        return total / count;
    }

    // ------------------------------------------------------------------
    // Вывод
    // ------------------------------------------------------------------

    void printHeader() {
        System.out.println("=== Библиотека ===");
        System.out.println("Команды: book, magazine, reader, batch, lend, return, renew, day, pay, list, report, help, quit");
        System.out.println(SEPARATOR);
    }

    void printFooter() {
        System.out.println(SEPARATOR);
        System.out.printf("Команд: %d, ошибок: %d, изданий: %d, читателей: %d, день: %d%n", commandCount, errorCount,
                items.size(), readers.size(), today);
    }

    private void printHelp() {
        String text = """
                Каталог: book <название> <автор> <жанр> <экз.> [страниц], magazine <название> <выпуск> <экз.>
                Читатели: reader <имя>; выдачи: lend, return, renew <читатель> <издание>
                Время и штрафы: day <дней>, pay <читатель> <сумма>
                Отчёты: list, find, author, readers, overdue, authors, top, genres, report, csv, history
                """;
        System.out.print(text);
    }

    private void printItems(List<Item> list) {
        if (list.isEmpty()) {
            System.out.println("Ничего не найдено");
            return;
        }
        System.out.printf("%-4s %-9s %-24s %-14s %4s %4s%n", "№", "Вид", "Название", "Жанр", "Всего", "Есть");
        for (Item item : list) {
            System.out.printf("%-4d %-9s %-24s %-14s %5d %4d%n", item.getId(), item.kind(), item.getTitle(),
                    item.getGenre().getLabel(), item.getCopies(), item.getAvailable());
        }
        System.out.println("Всего изданий: " + list.size());
    }

    private void printReaders() {
        if (readers.isEmpty()) {
            System.out.println("Читателей нет");
            return;
        }
        for (Reader reader : readers) {
            System.out.printf("№%d %-16s на руках: %d, штраф: %s%n", reader.getId(), reader.getName(),
                    countActiveLoans(reader.getId()), format(reader.getFine()));
        }
    }

    private void printOverdue() {
        int count = 0;
        for (Loan loan : loans) {
            if (!loan.isReturned() && loan.lateDays(today) > 0) {
                Reader reader = requireReader(loan.getReaderId());
                Item item = requireItem(loan.getItemId());
                System.out.printf("Просрочено на %d дн.: %s — %s%n", loan.lateDays(today), item.getTitle(),
                        reader.getName());
                count++;
            }
        }
        if (count == 0) {
            System.out.println("Просрочек нет");
        }
    }

    private void printTopReaders(int limit) {
        if (limit < 1) {
            throw new LibraryException("количество должно быть положительным");
        }
        Map<Integer, Long> byReader = loans.stream()
                .collect(Collectors.groupingBy(Loan::getReaderId, Collectors.counting()));
        List<Map.Entry<Integer, Long>> ranking = new ArrayList<>(byReader.entrySet());
        ranking.sort(Map.Entry.<Integer, Long>comparingByValue().reversed().thenComparing(Map.Entry.comparingByKey()));
        int place = 1;
        for (Map.Entry<Integer, Long> entry : ranking) {
            if (place > limit) {
                break;
            }
            Reader reader = requireReader(entry.getKey());
            System.out.printf("%d. %s — выдач: %d%n", place, reader.getName(), entry.getValue());
            place++;
        }
        if (ranking.isEmpty()) {
            System.out.println("Выдач ещё не было");
        }
    }

    private void printGenres() {
        Map<Genre, Integer> counts = new EnumMap<>(Genre.class);
        for (Item item : items) {
            counts.merge(item.getGenre(), item.getCopies(), Integer::sum);
        }
        for (Genre genre : Genre.values()) {
            System.out.printf("%-14s %d экз.%n", genre.getLabel(), counts.getOrDefault(genre, 0));
        }
    }

    private void printReport() {
        System.out.println("=== Отчёт ===");
        System.out.println("Изданий: " + items.size() + ", читателей: " + readers.size());
        System.out.println("Выдано: " + stats.lent + ", возвращено: " + stats.returned + ", продлено: " + stats.renewed);
        System.out.println("Штрафы начислены: " + format(stats.finesCharged) + ", оплачены: " + format(stats.finesPaid)
                + ", долг: " + format(stats.balance()));
        int active = 0;
        for (Loan loan : loans) {
            if (!loan.isReturned()) {
                active++;
            }
        }
        System.out.println("Сейчас на руках: " + active);
        Fine biggest = maxOf(fines);
        System.out.println("Начислений штрафа: " + fines.size()
                + (biggest == null ? "" : ", самое крупное: " + format(biggest.amount()) + " (день " + biggest.day() + ")"));
        Map<String, Integer> byKind = new LinkedHashMap<>();
        for (Item item : items) {
            byKind.merge(item.kind(), item.getCopies(), Integer::sum);
        }
        for (Map.Entry<String, Integer> entry : byKind.entrySet()) {
            System.out.println("  " + entry.getKey() + ": " + entry.getValue() + " экз.");
        }
        String owners = readers.stream()
                .filter(r -> r.getFine() > 0)
                .sorted(Comparator.comparing(Reader::getName))
                .map(r -> r.getName() + " (" + format(r.getFine()) + ")")
                .collect(Collectors.joining(", "));
        System.out.println("Должники: " + (owners.isEmpty() ? "нет" : owners));
    }

    private void printHistory() {
        int shown = 0;
        for (String entry : journal) {
            shown++;
            System.out.println(shown + ". " + entry);
        }
    }

    private String format(double value) {
        return String.format("%.2f", value);
    }

    static <T extends Comparable<T>> T maxOf(List<T> values) {
        T best = null;
        for (T value : values) {
            if (best == null || value.compareTo(best) > 0) {
                best = value;
            }
        }
        return best;
    }
}

record Fine(int readerId, double amount, int day) implements Comparable<Fine> {
    @Override
    public int compareTo(Fine other) {
        return Double.compare(amount, other.amount);
    }
}

enum Genre {
    FICTION("Роман"),
    SCIENCE("Наука"),
    HISTORY("История"),
    KIDS("Детям"),
    PERIODICAL("Периодика");

    private final String label;

    Genre(String label) {
        this.label = label;
    }

    String getLabel() {
        return label;
    }
}

abstract class Item implements Comparable<Item> {
    private final int id;
    private final String title;
    private final Genre genre;
    private int copies;
    private int available;

    Item(int id, String title, Genre genre, int copies) {
        this.id = id;
        this.title = title;
        this.genre = genre;
        this.copies = copies;
        this.available = copies;
    }

    abstract int loanDays();

    abstract String kind();

    abstract Item copy();

    int getId() {
        return id;
    }

    String getTitle() {
        return title;
    }

    Genre getGenre() {
        return genre;
    }

    int getCopies() {
        return copies;
    }

    int getAvailable() {
        return available;
    }

    void take() {
        available--;
    }

    void putBack() {
        available++;
    }

    void addCopies(int more) {
        copies += more;
        available += more;
    }

    protected void restore(int totalCopies, int freeCopies) {
        this.copies = totalCopies;
        this.available = freeCopies;
    }

    @Override
    public int compareTo(Item other) {
        return title.compareTo(other.title);
    }
}

class Book extends Item {
    private final String author;
    private final int pages;

    Book(int id, String title, Genre genre, int copies, String author, int pages) {
        super(id, title, genre, copies);
        this.author = author;
        this.pages = pages;
    }

    String getAuthor() {
        return author;
    }

    int getPages() {
        return pages;
    }

    @Override
    int loanDays() {
        return pages > 500 ? Main.LOAN_DAYS + 7 : Main.LOAN_DAYS;
    }

    @Override
    String kind() {
        return "книга";
    }

    @Override
    Item copy() {
        Book book = new Book(getId(), getTitle(), getGenre(), getCopies(), author, pages);
        book.restore(getCopies(), getAvailable());
        return book;
    }
}

class Magazine extends Item {
    private final int issue;

    Magazine(int id, String title, Genre genre, int copies, int issue) {
        super(id, title, genre, copies);
        this.issue = issue;
    }

    int getIssue() {
        return issue;
    }

    @Override
    int loanDays() {
        return 7;
    }

    @Override
    String kind() {
        return "журнал";
    }

    @Override
    Item copy() {
        Magazine magazine = new Magazine(getId(), getTitle(), getGenre(), getCopies(), issue);
        magazine.restore(getCopies(), getAvailable());
        return magazine;
    }
}

class Reader {
    private final int id;
    private final String name;
    private double fine;

    Reader(int id, String name) {
        this.id = id;
        this.name = name;
    }

    int getId() {
        return id;
    }

    String getName() {
        return name;
    }

    double getFine() {
        return fine;
    }

    void addFine(double amount) {
        fine += amount;
    }
}

class Loan {
    private final int readerId;
    private final int itemId;
    private final int since;
    private int due;
    private int renewals;
    private boolean returned;
    private int returnedOn;

    Loan(int readerId, int itemId, int since, int due) {
        this.readerId = readerId;
        this.itemId = itemId;
        this.since = since;
        this.due = due;
    }

    int getReaderId() {
        return readerId;
    }

    int getItemId() {
        return itemId;
    }

    int getSince() {
        return since;
    }

    int getDue() {
        return due;
    }

    int getRenewals() {
        return renewals;
    }

    boolean isReturned() {
        return returned;
    }

    int getReturnedOn() {
        return returnedOn;
    }

    void extend(int days) {
        due += days;
        renewals++;
    }

    void markReturned(int day) {
        returned = true;
        returnedOn = day;
    }

    int lateDays(int today) {
        int end = returned ? returnedOn : today;
        return Math.max(0, end - due);
    }
}

class LibraryException extends RuntimeException {
    LibraryException(String message) {
        super(message);
    }
}

class ImportException extends Exception {
    ImportException(String message) {
        super(message);
    }
}

class InputException extends RuntimeException {
    InputException(String message) {
        super(message);
    }
}
