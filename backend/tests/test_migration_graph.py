"""Validate migration metadata without importing migrations or connecting to a DB."""

import ast
from pathlib import Path
import unittest


VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def validate_graph(sources):
    revisions = {}
    for filename, source in sources:
        metadata = {}
        for node in ast.parse(source, filename=filename).body:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id in (
                    "revision", "down_revision", "depends_on"
                ):
                    metadata[target.id] = ast.literal_eval(node.value)
        revision = metadata["revision"]
        if not isinstance(revision, str) or not revision:
            raise ValueError(f"Invalid revision in {filename}")
        if revision in revisions:
            raise ValueError(
                f"Duplicate revision {revision}: {revisions[revision][0]} and {filename}"
            )
        references = []
        for field in ("down_revision", "depends_on"):
            value = metadata[field]
            if value is None:
                continue
            values = (value,) if isinstance(value, str) else value
            if not isinstance(values, (tuple, list)) or not all(
                isinstance(item, str) and item for item in values
            ):
                raise ValueError(f"Invalid {field} in {filename}")
            references.extend((field, item) for item in values)
        revisions[revision] = (filename, references)

    for revision, (filename, references) in revisions.items():
        for field, parent in references:
            if parent not in revisions:
                raise ValueError(f"Unresolved {field} {parent} in {filename} ({revision})")

    visited = set()
    active = []

    def visit(revision):
        if revision in active:
            cycle = active[active.index(revision):] + [revision]
            raise ValueError("Cycle: " + " -> ".join(cycle))
        if revision in visited:
            return
        active.append(revision)
        for _, parent in revisions[revision][1]:
            visit(parent)
        active.pop()
        visited.add(revision)

    for revision in revisions:
        visit(revision)
    return revisions


class MigrationGraphTests(unittest.TestCase):
    def test_repository_migrations(self):
        paths = sorted(VERSIONS.glob("*.py"))
        self.assertTrue(paths, "No migration files found")
        validate_graph((str(path), path.read_text(encoding="utf-8-sig")) for path in paths)

    def source(self, revision, parent=None, dependency=None):
        return (
            f"revision: str = {revision!r}\n"
            f"down_revision = {parent!r}\ndepends_on = {dependency!r}\n"
        )

    def test_duplicate_revisions_report_both_files(self):
        with self.assertRaisesRegex(ValueError, "Duplicate revision a: first.py and second.py"):
            validate_graph([("first.py", self.source("a")), ("second.py", self.source("a"))])

    def test_unresolved_references(self):
        for parent, dependency, field in (
            ("missing", None, "down_revision"),
            (None, ("missing",), "depends_on"),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, f"Unresolved {field}"):
                validate_graph([("a.py", self.source("a", parent, dependency))])

    def test_cycles_include_dependency_edges(self):
        for parent, dependency in (("b", None), (None, ("b",))):
            with self.subTest(parent=parent), self.assertRaisesRegex(ValueError, "Cycle: a -> b -> a"):
                validate_graph([
                    ("a.py", self.source("a", parent, dependency)),
                    ("b.py", self.source("b", "a")),
                ])
        with self.assertRaisesRegex(ValueError, "Cycle: a -> a"):
            validate_graph([("a.py", self.source("a", "a"))])

    def test_valid_merge_and_dependency_graph(self):
        validate_graph([
            ("base.py", self.source("base")),
            ("a.py", self.source("a", "base")),
            ("b.py", self.source("b", "base")),
            ("merge.py", self.source("merge", ("a", "b"), "base")),
        ])


if __name__ == "__main__":
    unittest.main()
