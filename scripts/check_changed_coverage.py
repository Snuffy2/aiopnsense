"""Check coverage of statements changed since a branch's merge base."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import TypedDict


class FileCoverage(TypedDict):
    """Coverage JSON fields used by the statement check."""

    missing_lines: list[int]


def changed_lines(diff: str) -> dict[str, set[int]]:
    """Read new-side line ranges from a zero-context Git diff.

    Args:
        diff (str): Git patch with zero context lines.

    Returns:
        dict[str, set[int]]: Changed line numbers by destination path.
    """
    files: dict[str, set[int]] = {}
    path = ""
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            path = ""
        elif not path and line.startswith("+++ b/"):
            path = line[6:].removesuffix("\t")
        elif path and (match := re.match(r"^@@ .* \+(\d+)(?:,(\d+))? @@", line)):
            start = int(match[1])
            count = int(match[2]) if match[2] is not None else 1
            files.setdefault(path, set()).update(range(start, start + count))
    return files


def uncovered_lines(
    changed: dict[str, set[int]], coverage: dict[str, FileCoverage]
) -> list[tuple[str, int]]:
    """Find changed statements reported as missing by coverage.py.

    Args:
        changed (dict[str, set[int]]): Changed line numbers by path.
        coverage (dict[str, FileCoverage]): Coverage JSON file records.

    Returns:
        list[tuple[str, int]]: Sorted path and line pairs for missing statements.
    """
    return [
        (path, line)
        for path in sorted(changed.keys() & coverage.keys())
        for line in sorted(changed[path].intersection(coverage[path]["missing_lines"]))
    ]


def main() -> int:
    """Run or reuse pytest coverage and check the current worktree.

    Returns:
        int: Zero when covered, one for missing statements, or a tool error status.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", nargs="?", default="origin/main", help="Base Git ref")
    parser.add_argument("--coverage-json", type=Path, help="Reuse coverage JSON for this tree")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]

    try:
        base = subprocess.check_output(
            ["git", "merge-base", "--", args.base, "HEAD"], cwd=root, text=True
        ).strip()
        diff = subprocess.check_output(
            [
                "git",
                "-c",
                "core.quotePath=false",
                "diff",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                "--src-prefix=a/",
                "--dst-prefix=b/",
                "-U0",
                base,
                "--",
                "aiopnsense/",
            ],
            cwd=root,
            text=True,
        )
        with TemporaryDirectory() as directory:
            report = args.coverage_json
            if report is None:
                report = Path(directory) / "coverage.json"
                subprocess.run(
                    [sys.executable, "-m", "pytest", f"--cov-report=json:{report}"],
                    cwd=root,
                    env={**os.environ, "PYTHONPATH": str(root)},
                    check=True,
                )
            missing = uncovered_lines(changed_lines(diff), json.loads(report.read_text())["files"])
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"{parser.prog}: {exc}\n")

    for path, line in missing:
        print(f"{path}:{line}")
    if not missing:
        print("No uncovered changed statements.")
    return int(bool(missing))


if __name__ == "__main__":
    sys.exit(main())
