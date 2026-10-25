import ast
from pathlib import Path


def test_discovery_does_not_import_sqlite3() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "cursor_portability" / "discovery"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "sqlite3", f"{path} imports sqlite3"
            if isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
                raise AssertionError(f"{path} imports sqlite3")
