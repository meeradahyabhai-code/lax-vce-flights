"""Unit tests for the excursion screenshot parser endpoint (api/parse_excursion.py).

These cover the deterministic, non-LLM logic (fence stripping, prompt contract).
The end-to-end vision accuracy is covered by the eval harness in
evals/parse_excursion_eval.py, which is run on demand against the live endpoint.
"""

import importlib.util
import json
import os

spec = importlib.util.spec_from_file_location(
    "parse_excursion", os.path.join(os.path.dirname(__file__), "api", "parse_excursion.py")
)
parse_excursion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parse_excursion)


def test_strip_fences_plain_json():
    assert parse_excursion.strip_fences('{"a": 1}') == '{"a": 1}'


def test_strip_fences_with_json_fence():
    raw = '```json\n{"a": 1}\n```'
    assert json.loads(parse_excursion.strip_fences(raw)) == {"a": 1}


def test_strip_fences_with_bare_fence():
    raw = '```\n{"title": "Old Town Walk"}\n```'
    assert json.loads(parse_excursion.strip_fences(raw)) == {"title": "Old Town Walk"}


def test_strip_fences_handles_whitespace_and_trailing_fence():
    raw = '   ```json\n{"price": "45"}```   '
    assert json.loads(parse_excursion.strip_fences(raw)) == {"price": "45"}


def test_strip_fences_empty():
    assert parse_excursion.strip_fences("") == ""
    assert parse_excursion.strip_fences(None) == ""


def test_prompt_requests_required_fields():
    # The four fields the UI enforces must be in the extraction contract.
    for field in ("title", "date", "start_time", "duration", "price", "currency"):
        assert field in parse_excursion.PARSE_PROMPT


def test_prompt_does_not_guess():
    assert "Do not guess" in parse_excursion.PARSE_PROMPT
