"""OPT-04, OPT-05, OPT-07 через API (S8.3, метка `jdk`): кандидат → gate → HEAD, вывод не меняется."""

from __future__ import annotations

from urllib.parse import quote

import pytest

from tests.integration.conftest import wait_run

pytestmark = pytest.mark.jdk

PROGRAM = """\
import java.util.*;

public class Main {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        List<Integer> numbers = new ArrayList<>();
        while (in.hasNextInt()) {
            numbers.add(in.nextInt());
        }
        String label = "Total";
        int n = numbers.size();
        long acc = 0;
        for (int i = 0; i < n; i++) {
            Collections.sort(numbers);
            acc += numbers.get(i) * label.toUpperCase().length();
        }
        long total = acc;
        double a = Math.sqrt(total) + 1;
        double b = Math.sqrt(total) * 2;
        System.out.println(total + " " + a + " " + b);
    }
}
"""
INPUTS = ["3 1 2\n", "", "5\n", "9 8 7 6 5 4 3 2 1\n"]


def url(project, suffix: str) -> str:
    return f"/api/projects/{project['id']}{suffix}"


def run_kind(client, project, kind: str, **body) -> dict:
    response = client.post(url(project, "/runs"), json={"kind": kind, **body})
    assert response.status_code == 202, response.text
    return wait_run(client, response.json()["run_id"], 180)


def outputs(client, project) -> list[str]:
    return [run_kind(client, project, "run", stdin=s)["items"][0]["stdout"] for s in INPUTS]


@pytest.fixture
def program(client, make_project):
    project = make_project({"Main.java": PROGRAM}, name="more-fixes")
    for index, stdin in enumerate(INPUTS):
        client.post(url(project, "/scenarios"), json={"name": f"s{index}", "stdin": stdin})
    assert run_kind(client, project, "baseline")["status"] == "done"
    return project


def available(client, project) -> dict[str, str]:
    listed = client.get(url(project, "/fixes")).json()["fixes"]
    return {f["fix_id"]: f["finding_id"] for f in listed if f["available"]}


def test_the_three_fixes_are_offered(client, program):
    assert {"OPT-04", "OPT-05", "OPT-07"} <= set(available(client, program))


@pytest.mark.parametrize("fix_id", ["OPT-04", "OPT-05", "OPT-07"])
def test_each_fix_keeps_the_output_and_the_gate_green(client, program, fix_id):
    expected = outputs(client, program)
    finding = available(client, program)[fix_id]
    versions = client.get(url(program, "/versions")).json()
    response = client.post(
        url(program, f"/fixes/{quote(finding, safe=chr(47))}/apply"),
        json={"base_version_id": versions["head_version_id"]},
    )
    assert response.status_code == 202, response.text
    info = wait_run(client, response.json()["run_id"], 180)
    assert info["summary"]["version_status"] == "passed", info["summary"]
    assert info["summary"]["data"]["candidate"] == "promoted"
    assert outputs(client, program) == expected
    assert fix_id not in available(client, program)


def test_batch_optimization_applies_them_in_turn(client, program):
    expected = outputs(client, program)
    plan = client.post(url(program, "/optimize/plan"), json={}).json()
    assert {"OPT-04", "OPT-05", "OPT-07"} <= {s["fix_id"] for s in plan["steps"]}
    versions = client.get(url(program, "/versions")).json()
    response = client.post(
        url(program, "/optimize/apply"),
        json={
            "base_version_id": versions["head_version_id"],
            "step_ids": [s["step_id"] for s in plan["steps"]],
        },
    )
    assert response.status_code == 202, response.text
    info = wait_run(client, response.json()["run_id"], 300)
    assert info["status"] == "done", info
    assert outputs(client, program) == expected


# --- OPT-08, OPT-11, OPT-12 -----------------------------------------------------------------------------------

SECOND = """\
import java.util.*;
import java.util.stream.*;

public class Main {
    static long fib(int k) {
        if (k < 2) {
            return k;
        }
        return fib(k - 1) + fib(k - 2);
    }

    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        List<String> words = new ArrayList<>();
        while (in.hasNext()) {
            words.add(in.next());
        }
        Integer total = 0;
        for (String w : words) {
            total += w.length();
        }
        List<String> kept = words.stream().sorted().filter(w -> w.length() > 2).collect(Collectors.toList());
        System.out.println(total + " " + kept + " " + fib(Math.min(total, 25)));
    }
}
"""
SECOND_INPUTS = ["a bb ccc dddd\n", "", "hello world\n", "x\n"]


@pytest.fixture
def second(client, make_project):
    project = make_project({"Main.java": SECOND}, name="more-fixes-2")
    for index, stdin in enumerate(SECOND_INPUTS):
        client.post(url(project, "/scenarios"), json={"name": f"t{index}", "stdin": stdin})
    assert run_kind(client, project, "baseline")["status"] == "done"
    return project


def second_outputs(client, project) -> list[str]:
    return [run_kind(client, project, "run", stdin=s)["items"][0]["stdout"] for s in SECOND_INPUTS]


@pytest.mark.parametrize("fix_id", ["OPT-08", "OPT-11", "OPT-12"])
def test_second_group_keeps_output_and_gate(client, second, fix_id):
    assert fix_id in available(client, second)
    expected = second_outputs(client, second)
    finding = available(client, second)[fix_id]
    versions = client.get(url(second, "/versions")).json()
    response = client.post(
        url(second, f"/fixes/{quote(finding, safe=chr(47))}/apply"),
        json={"base_version_id": versions["head_version_id"]},
    )
    assert response.status_code == 202, response.text
    info = wait_run(client, response.json()["run_id"], 240)
    assert info["summary"]["version_status"] == "passed", info["summary"]
    assert info["summary"]["data"]["candidate"] == "promoted"
    assert second_outputs(client, second) == expected
    assert fix_id not in available(client, second)


# --- OPT-01 на примере со слайда 5 -----------------------------------------------------------------------------


def test_opt_01_on_the_slide_five_sample_keeps_every_scenario(client):
    project = client.post("/api/projects", json={"sample_id": "analyzer-pairs"}).json()
    baseline = run_kind(client, project, "baseline")
    total = baseline["summary"]["passed"]
    assert total >= 5
    listed = client.get(url(project, "/fixes")).json()["fixes"]
    [offer] = [f for f in listed if f["fix_id"] == "OPT-01" and f["available"]]
    assert offer["behavior_preserving"] is False
    preview = client.post(
        url(project, f"/fixes/{quote(offer['finding_id'], safe=chr(47))}/preview"), json={}
    )
    assert preview.status_code == 200, preview.text
    assert "computeIfAbsent" in preview.json()["diff"][0]["unified"]
    versions = client.get(url(project, "/versions")).json()
    response = client.post(
        url(project, f"/fixes/{quote(offer['finding_id'], safe=chr(47))}/apply"),
        json={"base_version_id": versions["head_version_id"]},
    )
    assert response.status_code == 202, response.text
    info = wait_run(client, response.json()["run_id"], 300)
    assert info["summary"]["version_status"] == "passed", info["summary"]
    assert info["summary"]["passed"] == total
    assert info["summary"]["data"]["candidate"] == "promoted"


# --- OPT-09: «найти или добавить» ------------------------------------------------------------------------------

COUNTING = """\
import java.util.*;

public class Main {
    static class Count {
        private final String text;
        private int times = 1;

        Count(String text) {
            this.text = text;
        }

        String getText() {
            return text;
        }

        int getTimes() {
            return times;
        }

        void increment() {
            times++;
        }
    }

    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        List<String> words = new ArrayList<>();
        while (in.hasNext()) {
            words.add(in.next());
        }
        List<Count> counts = new ArrayList<>();
        for (String w : words) {
            boolean found = false;
            for (Count k : counts) {
                if (k.getText().equals(w)) {
                    k.increment();
                    found = true;
                    break;
                }
            }
            if (!found) {
                counts.add(new Count(w));
            }
        }
        for (Count k : counts) {
            System.out.println(k.getText() + " " + k.getTimes());
        }
    }
}
"""
COUNTING_INPUTS = ["a b a c b a\n", "", "x\n", "p q r s t p q r s t u\n"]


def test_opt_09_keeps_output_order_and_the_gate_green(client, make_project):
    project = make_project({"Main.java": COUNTING}, name="find-or-add")
    for index, stdin in enumerate(COUNTING_INPUTS):
        client.post(url(project, "/scenarios"), json={"name": f"c{index}", "stdin": stdin})
    assert run_kind(client, project, "baseline")["status"] == "done"
    expected = [
        run_kind(client, project, "run", stdin=s)["items"][0]["stdout"] for s in COUNTING_INPUTS
    ]
    offer = available(client, project)
    assert "OPT-09" in offer
    plan = client.post(url(project, "/optimize/plan"), json={}).json()
    assert "OPT-09" in {s["fix_id"] for s in plan["steps"]}
    versions = client.get(url(project, "/versions")).json()
    response = client.post(
        url(project, f"/fixes/{quote(offer['OPT-09'], safe=chr(47))}/apply"),
        json={"base_version_id": versions["head_version_id"]},
    )
    assert response.status_code == 202, response.text
    info = wait_run(client, response.json()["run_id"], 240)
    assert info["summary"]["version_status"] == "passed", info["summary"]
    assert info["summary"]["data"]["candidate"] == "promoted"
    after = [
        run_kind(client, project, "run", stdin=s)["items"][0]["stdout"] for s in COUNTING_INPUTS
    ]
    assert after == expected
    assert "OPT-09" not in available(client, project)


# --- OPT-13: индекс для метода-поиска ----------------------------------------------------------------------------

REGISTRY = """\
import java.util.*;

public class Main {
    static class Item {
        private final int id;
        private int qty;

        Item(int id, int qty) {
            this.id = id;
            this.qty = qty;
        }

        int getId() {
            return id;
        }

        int getQty() {
            return qty;
        }

        void addQty(int more) {
            qty += more;
        }
    }

    private final List<Item> items = new ArrayList<>();

    private Item find(int id) {
        for (Item item : items) {
            if (item.getId() == id) {
                return item;
            }
        }
        return null;
    }

    void add(int id, int qty) {
        Item existing = find(id);
        if (existing != null) {
            existing.addQty(qty);
            return;
        }
        Item item = new Item(id, qty);
        items.add(item);
    }

    boolean delete(int id) {
        Item found = find(id);
        if (found == null) {
            return false;
        }
        items.remove(found);
        return true;
    }

    void run(Scanner in) {
        while (in.hasNext()) {
            String command = in.next();
            if (command.equals("add")) {
                add(in.nextInt(), in.nextInt());
            } else if (command.equals("del")) {
                System.out.println(delete(in.nextInt()) ? "deleted" : "missing");
            } else if (command.equals("get")) {
                Item item = find(in.nextInt());
                System.out.println(item == null ? "none" : "qty " + item.getQty());
            } else if (command.equals("sum")) {
                int sum = 0;
                for (Item item : items) {
                    sum += item.getQty();
                }
                System.out.println("sum " + sum);
            }
        }
    }

    public static void main(String[] args) {
        new Main().run(new Scanner(System.in));
    }
}
"""
REGISTRY_INPUTS = [
    "add 1 5\nadd 2 7\nadd 1 3\nget 1\nget 9\nsum\n",
    "add 4 1\ndel 4\nget 4\nadd 4 2\nget 4\ndel 8\nsum\n",
    "",
    "add 3 1\nadd 2 1\nadd 1 1\ndel 2\nadd 2 9\nget 2\nsum\nget 3\n",
]


def test_opt_13_keeps_output_and_the_gate_green(client, make_project):
    project = make_project({"Main.java": REGISTRY}, name="registry")
    for index, stdin in enumerate(REGISTRY_INPUTS):
        client.post(url(project, "/scenarios"), json={"name": f"r{index}", "stdin": stdin})
    assert run_kind(client, project, "baseline")["status"] == "done"
    expected = [
        run_kind(client, project, "run", stdin=s)["items"][0]["stdout"] for s in REGISTRY_INPUTS
    ]
    offer = available(client, project)
    assert "OPT-13" in offer
    preview = client.post(
        url(project, f"/fixes/{quote(offer['OPT-13'], safe=chr(47))}/preview"), json={}
    )
    assert preview.status_code == 200, preview.text
    assert "itemsById" in preview.json()["diff"][0]["unified"]
    versions = client.get(url(project, "/versions")).json()
    response = client.post(
        url(project, f"/fixes/{quote(offer['OPT-13'], safe=chr(47))}/apply"),
        json={"base_version_id": versions["head_version_id"]},
    )
    assert response.status_code == 202, response.text
    info = wait_run(client, response.json()["run_id"], 240)
    assert info["summary"]["version_status"] == "passed", info["summary"]
    assert info["summary"]["data"]["candidate"] == "promoted"
    after = [
        run_kind(client, project, "run", stdin=s)["items"][0]["stdout"] for s in REGISTRY_INPUTS
    ]
    assert after == expected
    assert "OPT-13" not in available(client, project)


# --- REF-03: ранний возврат ---------------------------------------------------------------------------------------


def test_ref_03_flattens_process_orders_and_keeps_every_scenario_green(client, demo_orders):
    assert run_kind(client, demo_orders, "baseline")["summary"]["passed"] == 11
    offer = available(client, demo_orders)
    assert "REF-03" in offer
    plan = client.post(url(demo_orders, "/optimize/plan"), json={}).json()
    assert "REF-03" not in {
        s["fix_id"] for s in plan["steps"]
    }  # рефакторинг — по одному, не в пакете
    preview = client.post(
        url(demo_orders, f"/fixes/{quote(offer['REF-03'], safe=chr(47))}/preview"), json={}
    )
    assert preview.status_code == 200, preview.text
    assert "continue;" in preview.json()["diff"][0]["unified"]
    versions = client.get(url(demo_orders, "/versions")).json()
    response = client.post(
        url(demo_orders, f"/fixes/{quote(offer['REF-03'], safe=chr(47))}/apply"),
        json={"base_version_id": versions["head_version_id"]},
    )
    assert response.status_code == 202, response.text
    info = wait_run(client, response.json()["run_id"], 240)
    assert info["summary"]["version_status"] == "passed", info["summary"]
    assert info["summary"]["data"]["candidate"] == "promoted"
    gate = run_kind(client, demo_orders, "gate")
    assert gate["summary"]["version_status"] == "passed" and gate["summary"]["passed"] == 11
    rules = {f["rule_id"] for f in client.get(url(demo_orders, "/findings")).json()["findings"]}
    assert "MAINT-03" not in rules or "REF-03" not in available(client, demo_orders)
