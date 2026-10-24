"""`GET /input-schema` (S6.8): схема ввода через API."""

from __future__ import annotations

import pytest

SCANNER = """\
import java.util.*;

public class Main {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        int n = in.nextInt();
        for (int i = 0; i < n; i++) {
            String name = in.next();
            double price = in.nextDouble();
            System.out.println(name + price);
        }
    }
}
"""


def schema(client, project, **params):
    response = client.get(f"/api/projects/{project['id']}/input-schema", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_counted_loop_is_a_repeat_with_a_count_reference(client, make_project):
    body = schema(client, make_project({"Main.java": SCANNER}))
    assert body["inferred"] is True
    first, repeat = body["root"]
    assert (first["kind"], first["type"]) == ("token", "int") and first["source"].startswith(
        "nextInt@L"
    )
    assert (repeat["kind"], repeat["count_ref"]) == ("repeat", "n")
    assert [(c["kind"], c["type"]) for c in repeat["children"]] == [
        ("token", "word"),
        ("token", "double"),
    ]


def test_command_loop_lists_the_commands_of_demo_orders(client, demo_orders):
    [loop, *_] = schema(client, demo_orders)["root"]
    assert loop["kind"] == "command_loop"
    assert {"add", "list", "stats", "quit"} <= set(loop["labels"])
    assert loop["children"]


def test_program_without_input_has_an_empty_schema(client, make_project):
    body = schema(
        client,
        make_project(
            {
                "Main.java": 'public class Main { public static void main(String[] a) { System.out.println("x"); } }'
            }
        ),
    )
    assert body == {"inferred": False, "root": []}


def test_schema_follows_the_requested_version(client, make_project):
    project = make_project({"Main.java": SCANNER})
    plain = "public class Main { public static void main(String[] a) { } }"
    client.put(
        f"/api/projects/{project['id']}/files/Main.java",
        json={"base_version_id": project["head_version_id"], "content": plain},
    )
    assert schema(client, project)["root"] == []
    old = schema(client, project, version_id=project["original_version_id"])
    assert old["inferred"] is True


@pytest.mark.parametrize("sample", ["analyzer-pairs", "demo-orders"])
def test_schema_of_samples_is_deterministic(client, sample):
    project = client.post("/api/projects", json={"sample_id": sample}).json()
    assert schema(client, project) == schema(client, project)
