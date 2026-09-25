"""Isolation tests for Hiring Signals P4: the hard line between the standalone
tab's saved searches and Job Finder's saved-search matcher.

Why there is a line at all. Job Finder's `saved_searches` table is scanned on a
schedule by `saved_search_matcher.py`, which scores what it finds against job
postings with a REAL model call and publishes the best matches to `event_outbox`,
from where they become a Today item and a Telegram message for a real user. The
tab's saved searches are a different thing (a typed role and a metro, which
nothing ever runs), so they live in their own table, `hiring_signal_searches`.
The separation is only worth anything while it holds in BOTH directions:

- **forward.** No module of the feature reads or writes `saved_searches`, calls
  `saved_searches_store`, imports or names the matcher, publishes to
  `event_outbox` (directly, through `applications_store.record_event`, or through
  an RPC) or emits a `job_registry.*` event. A hiring-signal row that reached the
  matcher's inputs, or a hiring-signal action that reached the outbox, would score
  something it was never meant to score, or push a notification nobody asked for.
- **backward.** The matcher (and the rest of the job side that could pick a row
  up: the saved-search store and routes, the outbox worker, the digest listener)
  never mentions `hiring_signal_searches` or any `hiring_signal_*` module, and no
  module outside the feature names the tab's table at all.

Four guards, each from a different angle (the same shape as
`test_hiring_signal_architecture.py`, which polices the network side of the hard
lines):

1. STATIC, on the source (this file's scanners). They parse with `ast`, never
   only grep, so they see through the ways round a text search: an alias
   (`from . import saved_search_matcher as m`, `import x.y as z`), an absolute
   import of the same module, `importlib`/`__import__`/`getattr`, a table name
   built from parts (`"saved_" + "searches"`, an f-string, `"".join`, a module
   constant) or not spelled at all (`supabase.table(name)` with a `name` that is
   not a literal or a module constant is itself a violation), and a method
   reference held in a variable (`t = supabase.table`). The feature may name only
   the tables of a short allowlist and no RPC, and may only READ the two registry
   tables (a function that names one may not spell an insert, update, upsert or
   delete); it may import only a short list of modules of the package -- from
   `applications_store` only two named functions, never the module itself (which
   also keeps every LLM client out: the tab spends no model call).
   Prose is not code: a docstring or a comment that EXPLAINS the separation may
   name what it separates (the feature's own do), exactly as in the network
   tests. The backward direction is stricter -- the job side's files may not even
   MENTION the feature, prose included.
2. SQL. The executable statements of every hiring-signal migration (comments
   stripped) name neither the job side's tables nor an event, and create no
   trigger or function that could carry a row from one side to the other.
3. RUNTIME. A whole session of the tab through the real routes -- search, save a
   search, list, save a post, list, delete both, status -- against a Supabase
   double that records every table, RPC and query verb it is asked for: only the
   allowlisted tables, no RPC but the credential decryption, and nothing but
   selects on the registry.
4. SELF-CHECK. The scanners are not trusted because they pass. Every scanner is
   also run over mutated copies of the real sources -- every way round found in
   review -- and must flag each one, and must stay quiet on what is innocent.
   (Separately, the same mutations were applied to the REAL files of a scratch
   copy of the tree and this file was run there: it failed on each.)

The scanners take SOURCE TEXT, not a path, so that the self-check can feed them a
mutation without touching any real file.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any, Self

import pytest
from fastapi.testclient import TestClient
from hiring_signal_fakes import FIXTURE_NOW, USER, FakeSupabase, World, load_fixture
from test_hiring_signal_architecture import dynamic_access_violations

from between_jobs.api import hiring_signal_search, hiring_signal_service
from between_jobs.api.app import app
from between_jobs.api.app_state import get_hiring_http_client, get_supabase
from between_jobs.api.auth import require_user_id

API_DIR = Path(hiring_signal_search.__file__).parent
ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
PACKAGE = "between_jobs"

FEATURE_PATHS = sorted(API_DIR.glob("hiring_signal*.py"))
"""Every module of the feature, found by name. The architecture test lists them
by hand and checks the list against the directory; this scans whatever the glob
finds, so a new module is covered without anyone remembering to add it."""

JOB_SIDE_MODULES = (
    "saved_search_matcher.py",
    "saved_searches_store.py",
    "saved_searches_routes.py",
    "outbox_store.py",
    "digest_listener.py",
)
"""The job side: what scans, stores, publishes or consumes Job Finder's saved
searches. None of it may know the feature exists."""

# ── what is forbidden ────────────────────────────────────────────────────

FORBIDDEN_MODULES = frozenset(
    {
        "saved_searches_store",
        "saved_searches_routes",
        "saved_search_matcher",
        "outbox_store",
        "digest_listener",
    }
)
"""Modules of the job side. The feature may not import any of them, however it
spells the import."""
FORBIDDEN_NAMES = FORBIDDEN_MODULES | {
    "saved_searches",
    "event_outbox",
    "record_event",
    "change_application_stage",
    "create_application",
    "run_matcher_forever",
    "run_match_tick",
}
"""Identifiers the feature may not use for anything: the table names, the
module names, the three functions of `applications_store` that write an outbox row
(`record_event`, and `change_application_stage` / `create_application`, which do
through the stage-change RPC and the create path), and the matcher's entry
points. (`MAX_SAVED_SEARCHES` and `to_saved_search` are the feature's own words
for its OWN saved searches and are not these.)"""
FORBIDDEN_STRING_RX = re.compile(
    r"\b(?:saved_searches(?:_store|_routes)?|saved_search_matcher|event_outbox"
    r"|outbox_store|digest_listener)\b|\bjob_registry\.",
    re.IGNORECASE,
)
"""In any string the feature's code builds: a job-side table or module name, or
the `job_registry.` prefix every job-registry event type carries
(`job_registry.match_found.v1`). `job_registry_postings` -- the registry table
the echo matching READS -- has an underscore there and is not this."""
HIRING_MENTION_RX = re.compile(r"hiring_signal", re.IGNORECASE)
"""What the job side may not name: any module or table of the feature."""

READ_ONLY_TABLES = frozenset({"job_registry_companies", "job_registry_postings"})
"""The registry tables the echo matching READS. They belong to the ATS poller and
the saved-search matcher reads them too, so the feature may select from them and
never write."""
WRITE_METHODS = frozenset({"insert", "update", "upsert", "delete"})
ALLOWED_TABLES = frozenset(
    {
        "hiring_signal_query_cache",
        "hiring_signal_saves",
        "hiring_signal_searches",
    }
    | READ_ONLY_TABLES
)
"""The only tables a module of the feature may name: its own three, plus the two
registry tables the echo matching READS (never writes: `READ_ONLY_TABLES`)."""
ALLOWED_RPCS: frozenset[str] = frozenset()
"""The feature's modules call no RPC. (The shared credential lookup decrypts a
key through one, on the feature's behalf -- see the runtime test.)"""

ALLOWED_PACKAGE_MODULES = frozenset(
    {
        "app_state",
        "applications_store",
        "auth",
        "company_tiers",
        "credential_resolver",
        "errors",
        "geo_gazetteer",
        "jobs_store",
        "models",
        "search_aggregation",
        "search_providers",
        "worker_supervision",
    }
)
"""The rest of the package the feature may import, besides its own modules: the
app wiring, the credential lookup, a read of an application and its job snapshot,
the shared job-search helpers the query and the parser build on, the request
models, and the loop runner every background worker shares (the cache purge's own
loop runs on it; it publishes nothing and calls no model). Nothing that publishes,
schedules work for the job side or calls a model is here, so adding one is a
decision somebody has to make by editing this list."""
ALLOWED_NAMES_FROM_MODULE = {
    "applications_store": frozenset({"ApplicationNotFound", "get_application"}),
}
"""`applications_store` also creates applications and writes `event_outbox` rows
(`record_event`, the stage-change RPC). The feature only READS one application, and
only by `from .applications_store import <these names>`: importing the module itself
(`from . import applications_store`, `import between_jobs.api.applications_store`)
would hand over every function in it through an attribute."""
FORBIDDEN_THIRD_PARTY = frozenset(
    {"openai", "anthropic", "litellm", "langchain", "langchain_core", "google.generativeai"}
)
"""No model client: the tab makes no LLM call (the design target is zero)."""

_TABLE_METHODS = frozenset({"table", "from_"})
_RPC_METHODS = frozenset({"rpc"})


# ── scanners (source text in, violations out) ────────────────────────────


def _parse(source: str) -> ast.Module:
    tree = ast.parse(source)
    assert isinstance(tree, ast.Module)
    return tree


def _prose_ids(tree: ast.AST) -> set[int]:
    """The string constants that are prose: a docstring or a bare string
    statement -- where the feature explains what it does NOT do."""
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


class _Folder:
    """Reads the text a string EXPRESSION builds out of constants.

    `fold` is lenient and OVER-approximates, for the question "does a forbidden
    word appear in what this builds": a literal, `'a' + 'b'`, an f-string, a
    `%`-format, `'a'.join([...])`, `'a{}'.format(b)`, a case method on one of
    those, or a module constant; a hole it cannot read contributes nothing and the
    pieces are kept apart by a space. `resolve` is strict, for the question "which
    table is this EXACTLY": a literal, a module constant, `+` and an f-string whose
    every part resolves -- and `None` for anything else, which the table rule
    refuses because a name that is not spelled cannot be checked."""

    def __init__(self, consts: dict[str, str]) -> None:
        self.consts = consts

    def resolve(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, ast.Name):
            return self.consts.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.resolve(node.left), self.resolve(node.right)
            return left + right if left is not None and right is not None else None
        if isinstance(node, ast.JoinedStr):
            parts = [
                self.resolve(v.value) if isinstance(v, ast.FormattedValue) else self.resolve(v)
                for v in node.values
            ]
            return None if any(p is None for p in parts) else "".join(p for p in parts if p)
        return None

    def fold(self, node: ast.AST) -> str | None:
        exact = self.resolve(node)
        if exact is not None:
            return exact
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.fold(node.left), self.fold(node.right)
            return left + right if left is not None and right is not None else None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            left = self.fold(node.left)
            if left is None:
                return None
            args = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
            return " ".join([left, *(self.fold(a) or "" for a in args)])
        if isinstance(node, ast.JoinedStr):
            return "".join(
                (self.fold(v.value) or "") + " "
                if isinstance(v, ast.FormattedValue)
                else self.fold(v) or ""
                for v in node.values
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            return self._fold_method(node, node.func)
        return None

    def _fold_method(self, call: ast.Call, func: ast.Attribute) -> str | None:
        base = self.fold(func.value)
        if base is None:
            return None
        if func.attr == "join" and call.args and isinstance(call.args[0], ast.List | ast.Tuple):
            return base.join((self.fold(e) or "") for e in call.args[0].elts)
        if func.attr == "format":
            return " ".join([base, *(self.fold(a) or "" for a in call.args)])
        if func.attr in {"lower", "upper", "casefold", "strip", "lstrip", "rstrip", "title"}:
            return base
        return None


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Top-level `NAME = <string expression>` bindings, in source order, so
    `supabase.table(_TABLE)` can be read. A name bound twice is dropped: it
    cannot be read statically."""
    folder = _Folder({})
    seen: set[str] = set()
    for stmt in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target, value = stmt.target, stmt.value
        if not isinstance(target, ast.Name) or value is None:
            continue
        if target.id in seen:
            folder.consts.pop(target.id, None)
            continue
        seen.add(target.id)
        text = folder.resolve(value)
        if text is not None:
            folder.consts[target.id] = text
    return folder.consts


def _identifiers(tree: ast.AST) -> Iterator[tuple[int, str]]:
    """Every identifier the code spells: names, attributes, definitions,
    arguments, keywords, and each part of an import."""
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Name):
            yield line, node.id
        elif isinstance(node, ast.Attribute):
            yield line, node.attr
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            yield line, node.name
        elif isinstance(node, ast.arg | ast.keyword):
            if node.arg is not None:
                yield line, node.arg
        elif isinstance(node, ast.alias):
            yield line, node.asname or ""
            for part in node.name.split("."):
                yield line, part
        elif isinstance(node, ast.ImportFrom):
            for part in (node.module or "").split("."):
                yield line, part
        elif isinstance(node, ast.Global | ast.Nonlocal):
            for name in node.names:
                yield line, name


def forbidden_name_violations(source: str) -> list[str]:
    """The feature may not spell any of `FORBIDDEN_NAMES` -- as an import (in any
    form, aliased or absolute), an attribute, a variable, a definition."""
    found = {
        f"line {line}: names {name!r}"
        for line, name in _identifiers(_parse(source))
        if name in FORBIDDEN_NAMES
    }
    return sorted(found)


def forbidden_string_violations(source: str, pattern: re.Pattern[str]) -> list[str]:
    """Any string the code BUILDS (not prose) that matches `pattern` -- read
    through `_Folder`, at every level of an expression, so a name assembled from
    pieces is seen both whole and in the pieces that make it up."""
    tree = _parse(source)
    folder = _Folder(_module_constants(tree))
    prose = _prose_ids(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in prose or isinstance(node, ast.Name):
            continue
        text = folder.fold(node)
        if text is not None and (match := pattern.search(text)):
            found.add(f"line {getattr(node, 'lineno', 0)}: builds a string with {match.group()!r}")
    return sorted(found)


def _scope_map(tree: ast.Module) -> dict[int, int]:
    """`id(node) -> id(the function, or the module, whose own body holds it)` for
    every node: a node inside a nested function belongs to THAT function."""
    scopes: dict[int, int] = {}

    def visit(node: ast.AST, scope: int) -> None:
        scopes[id(node)] = scope
        inner = (
            id(node)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda)
            else scope
        )
        for child in ast.iter_child_nodes(node):
            visit(child, inner)

    visit(tree, id(tree))
    return scopes


def store_access_violations(
    source: str,
    *,
    allowed_tables: frozenset[str] | None = None,
    allowed_rpcs: frozenset[str] | None = None,
    forbidden_rx: re.Pattern[str] | None = None,
    read_only_tables: frozenset[str] | None = None,
) -> list[str]:
    """`.table(...)` / `.from_(...)` and `.rpc(...)`. The name must be a literal
    or a module constant (a name that is not spelled cannot be checked, so it is
    refused) and, with `allowed_tables` / `allowed_rpcs`, be one of those, or,
    with `forbidden_rx`, not match it. A method held in a variable
    (`t = supabase.table`) is refused too: the call is where the name is checked.

    With `read_only_tables`, a function (or the top level of a module) that names
    one of them may not spell a write (`.insert` / `.update` / `.upsert` /
    `.delete`) anywhere in its own body. The verb is chained after the table call,
    but a query is often built in a variable first (`q = sb.table(...)`,
    `q.delete()`), so the rule is per scope and not per chain: over-strict inside a
    function on purpose, and the violation is reported at the table call, which is
    what a reader has to look at. What a scan of one function cannot see (a query
    builder handed to ANOTHER function that writes) the runtime test does: it
    records the verb of every call a whole session makes."""
    tree = _parse(source)
    folder = _Folder(_module_constants(tree))
    found: set[str] = set()
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    scopes = _scope_map(tree)
    writes_by_scope: dict[int, list[int]] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and n.attr in WRITE_METHODS:
            writes_by_scope.setdefault(scopes[id(n)], []).append(n.lineno)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in _TABLE_METHODS | _RPC_METHODS
            and id(node) not in called
        ):
            found.add(f"line {node.lineno}: .{node.attr} is used without being called")
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _TABLE_METHODS | _RPC_METHODS
        ):
            continue
        method = node.func.attr
        name = folder.resolve(node.args[0]) if node.args else None
        if name is None:
            found.add(f"line {node.lineno}: .{method}(...) is not given a literal name")
            continue
        allowed = allowed_rpcs if method in _RPC_METHODS else allowed_tables
        if allowed is not None and name not in allowed:
            found.add(f"line {node.lineno}: .{method}({name!r}) is not on the allowlist")
        if forbidden_rx is not None and forbidden_rx.search(name):
            found.add(f"line {node.lineno}: .{method}({name!r})")
        writes = writes_by_scope.get(scopes[id(node)])
        if read_only_tables is not None and name in read_only_tables and writes:
            found.add(
                f"line {node.lineno}: .{method}({name!r}) is read-only, but the same "
                f"scope spells a write (line {min(writes)})"
            )
    return sorted(found)


def _package_targets(node: ast.Import | ast.ImportFrom) -> Iterator[tuple[str, list[str]]]:
    """`(module of this package, names imported from it)` for an import that
    reaches into `between_jobs.api`, spelled relatively or absolutely; an import
    that leaves the package is not one. `".."` stands for an import out of the
    api package, `""` for an import of the bare package."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            parts = alias.name.split(".")
            if parts[0] == PACKAGE:
                yield (parts[2] if len(parts) > 2 else "", [])
        return
    parts = (node.module or "").split(".") if node.module else []
    if node.level == 0:
        if not parts or parts[0] != PACKAGE:
            return
        parts = parts[2:]  # between_jobs.api.<module>
    elif node.level > 1:
        yield ("..", [])
        return
    if parts:
        yield (parts[0], [a.name for a in node.names])
    else:  # `from . import a, b`: each name is a module
        for alias in node.names:
            yield (alias.name, [])


def dependency_violations(source: str) -> list[str]:
    """What the feature reaches into: its own modules, `ALLOWED_PACKAGE_MODULES`
    (and, from `applications_store`, only `ALLOWED_NAMES_FROM_MODULE`), and no model
    client. A star import hides what it brings in, so it is refused."""
    found: set[str] = set()
    for node in ast.walk(_parse(source)):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        if any(alias.name == "*" for alias in node.names):
            found.add(f"line {node.lineno}: star import")
        if isinstance(node, ast.Import):
            outside = [alias.name for alias in node.names]
        else:
            outside = [node.module or ""] if node.level == 0 else []
        for dotted in outside:
            if any(dotted == m or dotted.startswith(m + ".") for m in FORBIDDEN_THIRD_PARTY):
                found.add(f"line {node.lineno}: imports {dotted}, a model client")
        for module, names in _package_targets(node):
            if module in FORBIDDEN_MODULES or module.startswith("hiring_signal"):
                continue  # the first is named, more precisely, by `forbidden_name_violations`
            if module in {"", ".."}:
                found.add(f"line {node.lineno}: imports from outside the api package")
            elif module not in ALLOWED_PACKAGE_MODULES:
                found.add(f"line {node.lineno}: imports {module}, which is not on the list")
            elif module in ALLOWED_NAMES_FROM_MODULE:
                if not names:  # the module itself, not names out of it
                    allowed = sorted(ALLOWED_NAMES_FROM_MODULE[module])
                    found.add(
                        f"line {node.lineno}: imports the module {module} itself "
                        f"(only {allowed} may be imported, by name)"
                    )
                extra = sorted(set(names) - ALLOWED_NAMES_FROM_MODULE[module])
                if extra:
                    found.add(f"line {node.lineno}: imports {extra} from {module}")
    return sorted(found)


def feature_isolation_violations(source: str) -> list[str]:
    """Every rule for a module of the feature (forward direction)."""
    return (
        forbidden_name_violations(source)
        + forbidden_string_violations(source, FORBIDDEN_STRING_RX)
        + store_access_violations(
            source,
            allowed_tables=ALLOWED_TABLES,
            allowed_rpcs=ALLOWED_RPCS,
            read_only_tables=READ_ONLY_TABLES,
        )
        + dependency_violations(source)
        + dynamic_access_violations(source)
    )


def job_side_violations(source: str) -> list[str]:
    """Every rule for a module of the job side (backward direction): it may not
    mention the feature at all -- prose and comments included, by plain text --
    nor build the name of one of its modules or tables, nor reach for one by a
    computed table name or a dynamic import."""
    mentions = [
        f"line {number}: mentions the feature"
        for number, line in enumerate(source.splitlines(), start=1)
        if HIRING_MENTION_RX.search(line)
    ]
    return (
        mentions
        + forbidden_string_violations(source, HIRING_MENTION_RX)
        + store_access_violations(source, forbidden_rx=HIRING_MENTION_RX)
        + dynamic_access_violations(source)
    )


def tab_table_violations(source: str) -> list[str]:
    """For a module OUTSIDE the feature: it names the tab's table (in code)."""
    tree = _parse(source)
    folder = _Folder(_module_constants(tree))
    prose = _prose_ids(tree)
    rx = re.compile(r"hiring_signal_searches", re.IGNORECASE)
    return sorted(
        {
            f"line {getattr(node, 'lineno', 0)}: names the tab's table"
            for node in ast.walk(tree)
            if id(node) not in prose
            and not isinstance(node, ast.Name)
            and (text := folder.fold(node)) is not None
            and rx.search(text)
        }
    )


_SQL_LINE_COMMENT_RX = re.compile(r"--[^\n]*")
_SQL_BLOCK_COMMENT_RX = re.compile(r"/\*.*?\*/", re.DOTALL)
_SQL_FORBIDDEN_RX = re.compile(
    r"\bsaved_searches\b|\bevent_outbox\b|\bjob_registry\.|\bsaved_search_matcher\b"
    r"|\bcreate\s+(?:or\s+replace\s+)?(?:trigger|function|rule)\b|\bnotify\b|\bpg_notify\b"
    r"|\bcreate\s+publication\b",
    re.IGNORECASE,
)


def sql_violations(sql: str) -> list[str]:
    """The executable statements of a migration (comments stripped): no job-side
    table or event, and nothing that could CARRY a row across -- a trigger, a
    function, a rule, a notification, a publication."""
    code = _SQL_BLOCK_COMMENT_RX.sub(" ", _SQL_LINE_COMMENT_RX.sub(" ", sql))
    return sorted({m.group().lower() for m in _SQL_FORBIDDEN_RX.finditer(code)})


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── the real sources are clean ───────────────────────────────────────────


def test_the_glob_finds_the_whole_feature_including_the_p4_modules() -> None:
    names = {p.name for p in FEATURE_PATHS}
    assert {
        "hiring_signal_tab.py",
        "hiring_signal_searches_store.py",
        "hiring_signal_saves_store.py",
        "hiring_signal_service.py",
        "hiring_signal_routes.py",
        "hiring_signals.py",
    } <= names
    assert len(names) >= 12


@pytest.mark.parametrize("path", FEATURE_PATHS, ids=lambda p: p.name)
def test_no_module_of_the_feature_reaches_the_job_side(path: Path) -> None:
    assert feature_isolation_violations(read(path)) == []


@pytest.mark.parametrize("name", JOB_SIDE_MODULES)
def test_the_job_side_never_mentions_the_feature(name: str) -> None:
    assert job_side_violations(read(API_DIR / name)) == []


def test_no_module_outside_the_feature_names_the_tabs_table() -> None:
    """Not only the matcher: NO other module reads or writes `hiring_signal_
    searches`. It has exactly one owner, the store, and one door, the routes."""
    offenders = {
        path.name: violations
        for path in sorted(API_DIR.glob("*.py"))
        if not path.name.startswith("hiring_signal")
        and (violations := tab_table_violations(read(path)))
    }
    assert offenders == {}


def test_the_routes_are_the_only_door_to_the_tabs_store() -> None:
    importers = sorted(
        path.name
        for path in FEATURE_PATHS
        if any(
            module == "hiring_signal_searches_store"
            for node in ast.walk(_parse(read(path)))
            if isinstance(node, ast.Import | ast.ImportFrom)
            for module, _ in _package_targets(node)
        )
    )
    assert importers == ["hiring_signal_routes.py"]


def test_the_scan_is_looking_at_real_code() -> None:
    """Guards against the scanners passing because they saw nothing."""
    store = read(API_DIR / "hiring_signal_searches_store.py")
    assert _module_constants(_parse(store))["_TABLE"] == "hiring_signal_searches"
    # the table name is found through the module constant: with an empty allowlist
    # the store is flagged for it, and with the real one it is not
    assert store_access_violations(store, allowed_tables=frozenset())
    assert store_access_violations(store, allowed_tables=ALLOWED_TABLES) == []
    registry = read(API_DIR / "hiring_signal_registry.py")
    assert 'table("job_registry_postings")' in registry
    assert store_access_violations(registry, allowed_tables=frozenset())
    # the job side really does name what the feature must not, and would be flagged for it
    matcher = read(API_DIR / "saved_search_matcher.py")
    assert 'table("saved_searches")' in matcher and 'table("event_outbox")' in matcher
    assert '"job_registry.match_found.v1"' in matcher
    assert forbidden_string_violations(matcher, FORBIDDEN_STRING_RX)
    assert forbidden_name_violations(matcher)
    assert store_access_violations(matcher, allowed_tables=ALLOWED_TABLES)
    assert dependency_violations(matcher)
    # the routes really do import applications_store (from which only two names are allowed)
    service = read(API_DIR / "hiring_signal_service.py")
    assert "from .applications_store import ApplicationNotFound, get_application" in service
    # every allowlisted table and module is really used by the feature (no dead entries)
    tables: set[str] = set()
    modules: set[str] = set()
    for path in FEATURE_PATHS:
        tree = _parse(read(path))
        folder = _Folder(_module_constants(tree))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _TABLE_METHODS
                and node.args
                and (name := folder.resolve(node.args[0])) is not None
            ):
                tables.add(name)
            if isinstance(node, ast.Import | ast.ImportFrom):
                modules.update(m for m, _ in _package_targets(node))
    assert tables == ALLOWED_TABLES
    assert modules >= ALLOWED_PACKAGE_MODULES


# ── SQL ──────────────────────────────────────────────────────────────────

HIRING_MIGRATIONS = sorted(MIGRATIONS_DIR.glob("*hiring_signal*.sql"))


def test_the_hiring_signal_migrations_are_found() -> None:
    assert len(HIRING_MIGRATIONS) >= 4
    assert any("searches_constraints" in p.name for p in HIRING_MIGRATIONS)


@pytest.mark.parametrize("path", HIRING_MIGRATIONS, ids=lambda p: p.name)
def test_no_hiring_signal_migration_wires_a_table_into_the_job_side(path: Path) -> None:
    assert sql_violations(read(path)) == []


def test_the_sql_scan_reads_code_and_not_comments() -> None:
    """The migrations DO talk about the separation, in comments."""
    p1 = read(MIGRATIONS_DIR / "20260913045525_create_hiring_signal_tables.sql")
    assert "saved_search_matcher" in p1  # in a comment
    assert sql_violations(p1) == []
    assert sql_violations("-- saved_searches\n/* event_outbox */\nselect 1;") == []


@pytest.mark.parametrize(
    "statement",
    [
        "insert into public.event_outbox (user_id) select user_id from hiring_signal_searches;",
        "create trigger t after insert on public.hiring_signal_searches for each row execute "
        "function f();",
        "create or replace function public.copy_search() returns trigger as $$ begin end $$;",
        "insert into public.saved_searches (user_id, query) select user_id, query from x;",
        "select pg_notify('c', 'x');",
        "create publication p for table public.hiring_signal_searches;",
        "/* hidden */ insert into public.event_outbox values (1);",
        "select 'job_registry.match_found.v1';",
    ],
)
def test_a_migration_that_carries_a_row_across_is_flagged(statement: str) -> None:
    assert sql_violations(statement)


# ── the self-check, part 1: the ways round a naive scan ──────────────────

FORWARD_BYPASSES: dict[str, str] = {
    "import the store": "from .saved_searches_store import list_saved_searches",
    "import the matcher": "from .saved_search_matcher import run_match_tick",
    "import the matcher's entry point by name": "from .anything import run_matcher_forever",
    "import the routes": "from .saved_searches_routes import router as r1",
    "import a module by from-import": "from . import saved_search_matcher",
    "import a module and alias it": "from . import saved_search_matcher as m2",
    "import from the store under another name": "from .saved_searches_store import create as make",
    "absolute import": "import between_jobs.api.saved_searches_store",
    "absolute import, aliased": "import between_jobs.api.saved_searches_store as s3",
    "absolute from-import": "from between_jobs.api import saved_search_matcher as m4",
    "absolute from-import of a name": "from between_jobs.api.anything import saved_searches",
    "import the outbox worker": "from .outbox_store import run_worker_once",
    "import the digest listener": "from .digest_listener import handle_batch",
    "import the outbox writer": "from .applications_store import record_event",
    "import an application creator": "from .applications_store import create_application",
    "import the stage changer": "from .applications_store import change_application_stage",
    "import applications_store as a module": "from . import applications_store as _as1",
    "import applications_store as a module, absolute": (
        "import between_jobs.api.applications_store as _as2"
    ),
    "import applications_store as a module, absolute from-import": (
        "from between_jobs.api import applications_store as _as3"
    ),
    "the stage changer through an imported module": (
        "from . import applications_store as _as4\n_e1 = _as4.change_application_stage"
    ),
    "the application creator through an imported module": (
        "from . import applications_store as _as5\n_e2 = _as5.create_application"
    ),
    "the stage changer by attribute": "_e3 = holder.change_application_stage",
    "a write to the registry postings": (
        "_f1 = supabase.table('job_registry_postings').update({'status': 'closed'})"
    ),
    "a delete on the registry companies": (
        "_f2 = supabase.table('job_registry_companies').delete().eq('id', 'x')"
    ),
    "an insert into the registry postings": (
        "_f3 = supabase.table('job_registry_postings').insert({'title': 'x'})"
    ),
    "an upsert into the registry companies": (
        "_f4 = supabase.table('job_registry_companies').upsert({'name': 'x'})"
    ),
    "a write to the registry through a variable": (
        "_f5 = supabase.table('job_registry_postings')\n_f6 = _f5.update({'status': 'x'})"
    ),
    "a write to the registry, table name in a constant": (
        "_REG = 'job_registry_postings'\n_f7 = supabase.table(_REG).delete()"
    ),
    "a table looked up by __getattribute__": (
        "_g1 = supabase.__getattribute__('ta' + 'ble')('today_items')"
    ),
    "a table looked up by attrgetter": (
        "_g2 = operator.attrgetter('table')(supabase)('today_items')"
    ),
    "a table looked up by an imported attrgetter": "from operator import attrgetter as _ag",
    "setattr": "_g3 = setattr(supabase, 'table', None)",
    "star import": "from .applications_store import *",
    "an unlisted module of the package": "from .prepare_orchestrator import run_prepare",
    "a model client of the package": "from .llm_client import generate",
    "a third-party model client": "import openai",
    "a third-party model client, from-import": "from anthropic import Anthropic",
    "an import out of the package": "from ..elsewhere import thing",
    "the saved_searches table": "_a1 = supabase.table('saved_searches')",
    "the saved_searches table, built from parts": "_a2 = supabase.table('saved_' + 'searches')",
    "the saved_searches table, in an f-string": "_a3 = supabase.table(f'saved_{x}')",
    "the saved_searches table, joined": "_a4 = supabase.table(''.join(['saved_', 'searches']))",
    "the saved_searches table, %-formatted": "_a5 = supabase.table('saved_%s' % 'searches')",
    "the saved_searches table, from a variable": "_a6 = supabase.table(name)",
    "the saved_searches table, from a module constant": (
        "_OTHER = 'saved_searches'\n_a7 = supabase.table(_OTHER)"
    ),
    "the saved_searches table, from a rebound constant": (
        "_REBOUND = 'a'\n_REBOUND = 'saved_searches'\n_a8 = supabase.table(_REBOUND)"
    ),
    "the alias method of the client": "_a9 = supabase.from_('saved_searches')",
    "the table method held in a variable": "_b1 = supabase.table",
    "the table method passed on": "_b2 = list(map(supabase.table, ['hiring_signal_searches']))",
    "the outbox table": "_b3 = supabase.table('event_outbox').insert({})",
    "a table that is nobody's business": "_b4 = supabase.table('today_items')",
    "a job-side table not named in the rules": "_b5 = supabase.table('today_item_job_matches')",
    "an rpc": "_b6 = supabase.rpc('insert_high_fit_job_today_item', {})",
    "the stage-change rpc": "_b7 = supabase.rpc('change_application_stage', {})",
    "an rpc from a variable": "_b8 = supabase.rpc(fn_name, {})",
    "a job-registry event type": "_EVENT_1 = 'job_registry.match_found.v1'",
    "a job-registry event type, built": "_EVENT_2 = 'job_registry' + '.match_found'",
    "a job-registry event type, formatted": "_EVENT_3 = 'job_registry.{}.v1'.format(kind)",
    "the matcher's idempotency key": "_KEY = f'job_registry.match_found:{x}'",
    "a table name in a constant": "_JOB_TABLE = 'saved_searches'",
    "a variable named like the table": "saved_searches = []",
    "a variable named like the outbox": "event_outbox = []",
    "an argument named like the table": "def _c1(saved_searches):\n    return saved_searches",
    "a keyword named like the outbox": "_c2 = dict(event_outbox=1)",
    "a dynamic import": "import importlib",
    "a dynamic import from importlib": "from importlib import import_module",
    "a dynamic import by string": "_d1 = __import__('between_jobs.api.saved_search_matcher')",
    "getattr": "_d2 = getattr(supabase, 'table')('saved_searches')",
    "eval": "_d3 = eval('1')",
}
"""Each is appended (as its own block) to every module of the feature and must be
flagged there. The code is never run: the scanners read the syntax tree, so a
name that is never defined does not matter."""


def _with_blocks(source: str, blocks: dict[str, str]) -> tuple[str, dict[str, range]]:
    """`source` with each block appended, and the line range each one occupies."""
    lines = source.rstrip("\n").split("\n")
    spans: dict[str, range] = {}
    for label, block in blocks.items():
        lines.extend(["", ""])
        start = len(lines) + 1
        lines.extend(block.split("\n"))
        spans[label] = range(start, len(lines) + 1)
    return "\n".join(lines) + "\n", spans


def _unflagged(violations: list[str], spans: dict[str, range]) -> list[str]:
    flagged = {int(m.group(1)) for v in violations if (m := re.match(r"line (\d+):", v))}
    return [label for label, span in spans.items() if not flagged & set(span)]


@pytest.mark.parametrize("path", FEATURE_PATHS, ids=lambda p: p.name)
def test_every_way_round_the_scan_is_flagged_in_every_feature_module(path: Path) -> None:
    """All the bypasses are appended, as separate blocks, to each module in turn
    and each block must come back with a violation of its own (one scan per
    module, and a failure names the bypass that got through)."""
    source = read(path)
    assert feature_isolation_violations(source) == []
    mutated, spans = _with_blocks(source, FORWARD_BYPASSES)
    assert _unflagged(feature_isolation_violations(mutated), spans) == []


@pytest.mark.parametrize("label", list(FORWARD_BYPASSES))
def test_each_way_round_the_scan_is_flagged_on_its_own(label: str) -> None:
    """The same, one bypass at a time against the module that matters most, so
    a failure is a single named case and a bypass cannot be hidden by the
    violations of another block."""
    source = read(API_DIR / "hiring_signal_searches_store.py")
    mutated, spans = _with_blocks(source, {label: FORWARD_BYPASSES[label]})
    assert _unflagged(feature_isolation_violations(mutated), spans) == []


def test_each_rule_catches_its_own_case() -> None:
    """The rules overlap on purpose; this pins each on the case it exists for, so
    removing one is not hidden by another catching the same mutation."""
    store = read(API_DIR / "hiring_signal_searches_store.py")

    def mutated(code: str) -> str:
        return store + "\n\n" + code + "\n"

    assert forbidden_name_violations(mutated("saved_searches = 1"))
    assert forbidden_string_violations(mutated("_X = 'saved_' + 'searches'"), FORBIDDEN_STRING_RX)
    assert store_access_violations(mutated("_Y = sb.table(n)"), allowed_tables=ALLOWED_TABLES)
    assert store_access_violations(mutated("_Y = sb.table('x')"), allowed_tables=ALLOWED_TABLES)
    assert store_access_violations(mutated("_Y = sb.rpc('x', {})"), allowed_rpcs=ALLOWED_RPCS)
    assert dependency_violations(mutated("from .llm_client import generate"))
    assert dependency_violations(mutated("from .applications_store import record_event"))
    assert dependency_violations(mutated("from . import applications_store as _a"))
    assert dependency_violations(mutated("import between_jobs.api.applications_store"))
    assert store_access_violations(
        mutated("_Y = sb.table('job_registry_postings').update({})"),
        read_only_tables=READ_ONLY_TABLES,
    )
    assert forbidden_name_violations(mutated("_W = holder.change_application_stage"))
    assert dynamic_access_violations(mutated("_Z = getattr(o, 'x')"))
    assert dynamic_access_violations(mutated("_Z = o.__getattribute__('x')"))
    assert dynamic_access_violations(mutated("_Z = operator.attrgetter('x')"))
    # and none of those is flagged by a rule it does not belong to
    assert forbidden_name_violations(store) == []
    assert (
        store_access_violations(
            mutated("_Y = sb.table('job_registry_postings').select('id')"),
            read_only_tables=READ_ONLY_TABLES,
        )
        == []
    )
    assert dependency_violations(mutated("_X = 'saved_' + 'searches'")) == []
    assert forbidden_name_violations(mutated("_X = 'saved_' + 'searches'")) == []


def test_the_scanners_do_not_flag_what_is_innocent() -> None:
    """The feature's own words for its own saved searches, prose that explains
    the separation, a read of the two registry tables, a table held in a module
    constant, and a message that says `saved search` in words must all stay legal."""
    routes = read(API_DIR / "hiring_signal_routes.py")
    assert "MAX_SAVED_SEARCHES" in routes and "saved search" in routes  # real, and legal
    store = read(API_DIR / "hiring_signal_searches_store.py")
    assert "to_saved_search" in store
    innocent = store + (
        "\n\nasync def _fine(supabase, user_id):\n"
        "    '''Nothing here reads saved_searches or writes event_outbox: prose.'''\n"
        "    # a comment naming saved_search_matcher\n"
        "    await supabase.table('job_registry_postings').select('id').execute()\n"
        "    await supabase.table('job_registry_companies').select('id').execute()\n"
        "    return 'no saved search found for id'\n"
        "\n\n'''a bare string statement about job_registry.match_found.v1'''\n"
    )
    assert feature_isolation_violations(innocent) == []
    cache = read(API_DIR / "hiring_signal_cache.py")
    assert "saved_search_matcher" in cache  # the real docstring says it, in prose
    assert feature_isolation_violations(cache) == []


# ── the self-check, part 2: the other direction ──────────────────────────

BACKWARD_BYPASSES: dict[str, str] = {
    "import the searches store": "from .hiring_signal_searches_store import list_searches",
    "import the service": "from .hiring_signal_service import search_tab",
    "import a module and alias it": "from . import hiring_signal_service as hs",
    "absolute import": "import between_jobs.api.hiring_signal_tab",
    "absolute from-import": "from between_jobs.api import hiring_signal_routes as hr",
    "the table": "_a1 = supabase.table('hiring_signal_searches')",
    "the table, built from parts": "_a2 = supabase.table('hiring_' + 'signal_searches')",
    "the table, in an f-string": "_a3 = supabase.table(f'hiring_{\"signal\"}_searches')",
    "the table, from a variable": "_a4 = supabase.table(name)",
    "the table, through the alias method": "_a5 = supabase.from_('hiring_signal_searches')",
    "the table, in a constant": "_TABLE_1 = 'hiring_signal_searches'",
    "the saves table": "_a6 = supabase.table('hiring_signal_saves')",
    "the cache table, built": "_a7 = supabase.table('hiring_signal_' + 'query_cache')",
    "an rpc named for the feature": "_a8 = supabase.rpc('hiring_signal_something', {})",
    "a mention in a docstring": "def _b1():\n    '''Also scans hiring_signal_searches.'''",
    "a mention in a comment": "# see hiring_signal_searches",
    "a bare string statement": "'''hiring_signal_saves'''",
    "a dynamic import by string": "_d1 = __import__('between_jobs.api.hiring_signal_tab')",
    "importlib": "import importlib",
    "getattr": "_d2 = getattr(supabase, 'table')",
}
"""Each is appended (as its own block) to every module of the job side and must be
flagged there."""


@pytest.mark.parametrize("name", JOB_SIDE_MODULES)
def test_every_way_round_the_backward_scan_is_flagged_in_every_job_side_module(name: str) -> None:
    source = read(API_DIR / name)
    assert job_side_violations(source) == []
    mutated, spans = _with_blocks(source, BACKWARD_BYPASSES)
    assert _unflagged(job_side_violations(mutated), spans) == []


@pytest.mark.parametrize("label", list(BACKWARD_BYPASSES))
def test_each_way_round_the_backward_scan_is_flagged_on_its_own(label: str) -> None:
    source = read(API_DIR / "saved_search_matcher.py")
    mutated, spans = _with_blocks(source, {label: BACKWARD_BYPASSES[label]})
    assert _unflagged(job_side_violations(mutated), spans) == []


def test_the_job_sides_own_tables_are_legal_on_the_job_side() -> None:
    matcher = read(API_DIR / "saved_search_matcher.py")
    extra = "async def _x(sb):\n    return sb.table('event_outbox'), sb.table('saved_searches')\n"
    assert job_side_violations(matcher + "\n\n" + extra) == []


def test_a_module_outside_the_feature_that_names_the_tabs_table_is_flagged() -> None:
    app_source = read(API_DIR / "app.py")
    assert tab_table_violations(app_source) == []
    for code in (
        "_T = 'hiring_signal_searches'",
        "_T = 'hiring_signal_' + 'searches'",
        "_T = f'hiring_signal_{\"searches\"}'",
        "def _q(sb):\n    return sb.table('hiring_signal_searches')",
    ):
        assert tab_table_violations(app_source + "\n\n" + code + "\n"), code
    # what the app module really does name (the feature's routers) is fine
    assert "hiring_signal_router" in app_source


# ── runtime ──────────────────────────────────────────────────────────────


class _SpyTable:
    """A table that remembers WHICH verb (`select`, `insert`, `upsert`, `delete`)
    each query on it started with, then hands the call to the real fake table.
    (`update` has no fake: the feature never issues one, and a write the fake does
    not know is an error in itself.)"""

    def __init__(self, name: str, inner: Any, log: list[tuple[str, str]]) -> None:
        self._name, self._inner, self._log = name, inner, log

    def select(self, *columns: Any) -> Any:
        self._log.append((self._name, "select"))
        return self._inner.select(*columns)

    def insert(self, payload: Any) -> Any:
        self._log.append((self._name, "insert"))
        return self._inner.insert(payload)

    def upsert(self, payload: Any, **kwargs: Any) -> Any:
        self._log.append((self._name, "upsert"))
        return self._inner.upsert(payload, **kwargs)

    def delete(self) -> Any:
        self._log.append((self._name, "delete"))
        return self._inner.delete()

    def update(self, payload: Any) -> Any:
        self._log.append((self._name, "update"))
        raise AssertionError("the in-memory project has no `update`: the feature never issues one")


class SpySupabase(FakeSupabase):
    """The in-memory Supabase, remembering every table and RPC it was asked for
    and the verb of every query made on each table."""

    def __init__(self, tables: dict[str, Any]) -> None:
        super().__init__(tables)
        self.tables_asked: set[str] = set()
        self.rpcs_asked: list[str] = []
        self.verbs: list[tuple[str, str]] = []

    def table(self, name: str) -> Any:
        self.tables_asked.add(name)
        return _SpyTable(name, super().table(name), self.verbs)

    def rpc(self, fn: str, params: dict[str, Any]) -> Any:
        self.rpcs_asked.append(fn)
        return super().rpc(fn, params)

    def writes_to_read_only_tables(self) -> set[tuple[str, str]]:
        """Every `(table, verb)` of a registry table that is not a `select`."""
        return {(t, v) for t, v in self.verbs if t in READ_ONLY_TABLES and v != "select"}


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> Self:
        return cls.fromtimestamp(FIXTURE_NOW.timestamp(), tz=tz or UTC)


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")
    monkeypatch.delenv("DISABLE_HIRING_SIGNALS", raising=False)
    # the routes call the service without a clock: freeze the reference IT reads
    monkeypatch.setattr(hiring_signal_service, "datetime", _FrozenDatetime)
    yield
    app.dependency_overrides.clear()


def test_a_whole_session_of_the_tab_touches_only_the_allowed_tables_and_no_rpc_but_decrypt() -> (
    None
):
    world = World(
        responses={"firecrawl": load_fixture("firecrawl_role_posts.json")},
        registry_companies=[{"id": "co-n", "name": "Northwind Labs, Inc."}],
        registry_postings=[
            {
                "id": "p1",
                "company_id": "co-n",
                "title": "Software Engineer",
                "location": "Bengaluru, Karnataka",
                "status": "active",
                "first_seen": "2026-09-10T00:00:00+00:00",
            }
        ],
    )
    spy = SpySupabase(world.tables)
    app.dependency_overrides[get_supabase] = lambda: spy
    app.dependency_overrides[get_hiring_http_client] = lambda: world.http
    app.dependency_overrides[require_user_id] = lambda: USER
    client = TestClient(app)

    searched = client.post(
        "/hiring-signals/search",
        json={"query": "software engineer", "location": "Bengaluru", "freshness": "3days"},
    )
    assert searched.status_code == 200
    body = searched.json()
    assert body["counts"]["echoes_hidden"] == 1  # the registry lookup really ran
    activity_id = body["signals"][0]["activity_id"]

    created = client.post(
        "/hiring-signals/searches", json={"query": "software engineer", "location": "Bengaluru"}
    )
    assert created.status_code == 201
    listed = client.get("/hiring-signals/searches").json()["searches"]
    assert [s["id"] for s in listed] == [created.json()["id"]]
    saved = client.post("/hiring-signals/saves", json={"activity_id": activity_id})
    assert saved.status_code == 201
    assert len(client.get("/hiring-signals/saves").json()["saves"]) == 1
    assert client.delete(f"/hiring-signals/saves/{saved.json()['id']}").status_code == 204
    assert client.delete(f"/hiring-signals/searches/{created.json()['id']}").status_code == 204
    assert client.get("/hiring-signals/status").json() == {"enabled": True}

    assert spy.tables_asked == {
        "provider_credentials",  # the shared key lookup
        "hiring_signal_query_cache",
        "hiring_signal_searches",
        "hiring_signal_saves",
        "job_registry_companies",
        "job_registry_postings",
    }
    assert spy.tables_asked <= ALLOWED_TABLES | {"provider_credentials"}
    assert not spy.tables_asked & {"saved_searches", "event_outbox", "today_items"}
    assert set(spy.rpcs_asked) == {"decrypt_secret"}
    # the registry is only ever READ: every query made on its two tables was a select
    assert {v for t, v in spy.verbs if t in READ_ONLY_TABLES} == {"select"}
    assert spy.writes_to_read_only_tables() == set()


def test_the_runtime_spy_sees_a_write_to_a_registry_table() -> None:
    """The check above is only worth something if the spy would notice one: each
    write verb, on each registry table, is reported and a select is not."""
    spy = SpySupabase(World().tables)
    spy.table("job_registry_postings").select("id")
    assert spy.writes_to_read_only_tables() == set()
    spy.table("job_registry_postings").delete()
    spy.table("job_registry_companies").insert({"name": "x"})
    spy.table("job_registry_companies").upsert({"name": "x"}, on_conflict="name")
    with pytest.raises(AssertionError):
        spy.table("job_registry_postings").update({"status": "closed"})
    assert spy.writes_to_read_only_tables() == {
        ("job_registry_postings", "delete"),
        ("job_registry_companies", "insert"),
        ("job_registry_companies", "upsert"),
        ("job_registry_postings", "update"),
    }
    # a write to the feature's OWN table is not a write to a registry table
    spy2 = SpySupabase(World().tables)
    spy2.table("hiring_signal_saves").delete()
    assert spy2.writes_to_read_only_tables() == set()


def test_the_fake_project_has_no_job_side_tables_so_a_session_that_completes_never_asked() -> None:
    """The in-memory project has no `saved_searches` or `event_outbox` table (asking
    for one is a `KeyError`), which is what makes the runtime test above a proof
    and not a hope."""
    world = World()
    assert "saved_searches" not in world.tables and "event_outbox" not in world.tables
    with pytest.raises(KeyError):
        world.supabase.table("saved_searches")
