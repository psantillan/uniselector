"""Ground rule 1: the orchestrator uses the DataHandles protocol only."""
import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "uniselector"
FORBIDDEN = ("datahandles.runtime", "datahandles.core", "packages.runtime", "packages.core")


def imports(path: Path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
            yield from (f"{node.module}.{alias.name}" for alias in node.names)


def test_the_orchestrator_imports_no_runtime_and_no_core():
    files = sorted(PACKAGE.rglob("*.py"))
    assert len(files) > 8, "the scan found no package"
    bad = [(str(f.relative_to(PACKAGE)), name) for f in files for name in imports(f)
           if any(name == x or name.startswith(x + ".") for x in FORBIDDEN)]
    assert bad == []


def test_the_scan_finds_a_forbidden_import(tmp_path):
    """The scan can fail: it sees each form of the import."""
    sample = tmp_path / "bad.py"
    sample.write_text("from datahandles import runtime\nimport datahandles.core.schema\nfrom datahandles.sdk import connect\n")
    found = [n for n in imports(sample) if any(n == x or n.startswith(x + ".") for x in FORBIDDEN)]
    assert found == ["datahandles.runtime", "datahandles.core.schema"]
