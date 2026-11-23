"""Tests for the local changed-statement coverage command."""

from pathlib import Path
from runpy import run_path


def test_changed_statement_coverage() -> None:
    """Ignore deletions, nonstatements, branch arcs and unchanged statements."""
    script = run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/check_changed_coverage.py")
    )
    changed = script["changed_lines"](
        "diff --git a/aiopnsense/example.py b/aiopnsense/example.py\n"
        "+++ b/aiopnsense/example.py\n"
        "@@ -1 +1,2 @@\n-old\n+new\n+# comment\n"
        "@@ -8,2 +8,0 @@\n-removed\n-also removed\n"
        "@@ -12 +11 @@\n-old\n+new\n"
        "diff --git a/aiopnsense/deleted.py b/aiopnsense/deleted.py\n"
        "+++ /dev/null\n@@ -1 +0,0 @@\n-deleted\n"
    )
    assert changed == {"aiopnsense/example.py": {1, 2, 11}}
    assert script["uncovered_lines"](
        changed,
        {"aiopnsense/example.py": {"missing_lines": [1, 8], "missing_branches": [[11, 12]]}},
    ) == [("aiopnsense/example.py", 1)]
    assert script["uncovered_lines"](changed, {}) == []
