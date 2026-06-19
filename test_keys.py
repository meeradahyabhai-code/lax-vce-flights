"""Credential validation tests.

Live network checks are gated behind RUN_KEY_CHECKS=1 so the default suite (and
CI, which doesn't hold the OpenAI/Places keys) stays green. The always-on prod
safety net is the scheduled `ai-health` workflow, which needs no secrets.

Run the full credential check locally with:
    RUN_KEY_CHECKS=1 python -m pytest test_keys.py -v
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

_RUN = os.environ.get("RUN_KEY_CHECKS") == "1"


@pytest.mark.skipif(not _RUN, reason="set RUN_KEY_CHECKS=1 to validate live credentials (network)")
def test_required_credentials_valid():
    import check_keys
    assert check_keys.run(offline=False) == 0, "a required credential failed live validation"


@pytest.mark.skipif(not _RUN, reason="set RUN_KEY_CHECKS=1 to validate prod AI (network)")
def test_prod_ai_live():
    import check_live_ai
    ok, detail = check_live_ai.check_summary(check_live_ai.DEFAULT_BASE)
    assert ok, f"prod AI health failed: {detail}"
