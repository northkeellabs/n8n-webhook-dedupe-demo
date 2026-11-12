"""Static checks on the exported workflow JSON (no Docker needed)."""

import json

import pytest

from dedupe_demo.config import ROOT

FILES = ["naive.json", "builtin.json", "fixed.json"]


def load(name):
    return json.loads((ROOT / "workflows" / name).read_text(encoding="utf-8"))


def node(wf, type_suffix):
    return [n for n in wf["nodes"] if n["type"].endswith(type_suffix)]


@pytest.mark.parametrize("name", FILES)
def test_importable_shape(name):
    wf = load(name)
    assert wf["id"] and wf["name"] and wf["nodes"] and wf["connections"]
    names = {n["name"] for n in wf["nodes"]}
    for src, conns in wf["connections"].items():
        assert src in names
        for outputs in conns["main"]:
            for c in outputs:
                assert c["node"] in names


@pytest.mark.parametrize("name", FILES)
def test_no_secrets_or_local_paths(name):
    text = (ROOT / "workflows" / name).read_text(encoding="utf-8")
    assert "password" not in text.lower()
    assert "C:\\" not in text and "/Users/" not in text and "/home/" not in text
    for n in load(name)["nodes"]:
        for cred in n.get("credentials", {}).values():
            assert set(cred) == {"id", "name"}


@pytest.mark.parametrize("name,path", [("naive.json", "dedupe-naive"), ("builtin.json", "dedupe-builtin"), ("fixed.json", "dedupe-fixed")])
def test_each_workflow_has_its_own_path(name, path):
    (hook,) = node(load(name), ".webhook")
    assert hook["parameters"]["path"] == path


def test_fixed_responds_immediately_with_202():
    (hook,) = node(load("fixed.json"), ".webhook")
    p = hook["parameters"]
    assert p["responseMode"] == "onReceived"
    assert p["options"]["responseCode"]["values"]["customCode"] == 202


def test_naive_and_builtin_respond_when_last_node_finishes():
    for name in ("naive.json", "builtin.json"):
        (hook,) = node(load(name), ".webhook")
        assert hook["parameters"]["responseMode"] == "lastNode"


def test_builtin_uses_remove_duplicates_previous_executions():
    (rd,) = node(load("builtin.json"), ".removeDuplicates")
    assert rd["parameters"]["operation"] == "removeItemsSeenInPreviousExecutions"
    assert rd["parameters"]["logic"] == "removeItemsWithAlreadySeenKeyValues"
    assert "x-event-id" in rd["parameters"]["dedupeValue"]


def test_fixed_llm_error_marks_failed():
    wf = load("fixed.json")
    llm = next(n for n in wf["nodes"] if n["name"] == "LLM call")
    assert llm["onError"] == "continueErrorOutput"
    error_output = wf["connections"]["LLM call"]["main"][1]
    assert [c["node"] for c in error_output] == ["Mark failed"]
