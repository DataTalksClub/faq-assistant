"""The packed search index is Python-version-tagged: an index built on one
minor version fails to load on another. Keep every place that picks the Python
version on the Lambda runtime's version, so CI builds the index on the same
interpreter the function runs (uv otherwise may pick any installed Python that
satisfies requires-python, e.g. a uv-managed 3.13 on the self-hosted runner).
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def lambda_runtime_version() -> str:
    template = (ROOT / "template.yaml").read_text(encoding="utf-8")
    match = re.search(r"^\s*Runtime:\s*python(\d+\.\d+)\s*$", template, re.MULTILINE)
    assert match, "python Runtime not found in template.yaml"
    return match.group(1)


def test_python_version_file_matches_lambda_runtime():
    pinned = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert pinned == lambda_runtime_version()


def test_workflows_set_up_the_lambda_runtime_python():
    runtime = lambda_runtime_version()
    for name in ("deploy.yml", "test.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        versions = re.findall(r'python-version:\s*"?([\d.]+)"?', text)
        assert versions, f"{name} does not set up Python"
        assert set(versions) == {runtime}, f"{name}: {versions} != {runtime}"


def test_interpreter_matches_lambda_runtime():
    # uv honours .python-version, so the test interpreter is the build interpreter.
    assert f"{sys.version_info.major}.{sys.version_info.minor}" == lambda_runtime_version()
