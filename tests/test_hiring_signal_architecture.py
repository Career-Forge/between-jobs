"""Architecture tests for Hiring Signals P3: WHERE network I/O may live, and
what it may ever be pointed at.

The hard line of this feature is that no code path -- server side -- ever
requests a `linkedin.com` url. The only things allowed to touch a network are
(a) the four search-provider APIs, at their fixed hosts, and (b) the user's own
browser loading LinkedIn's public embed iframe (a string this backend builds
but never fetches). Four guards enforce that, each from a different angle:

1. RUNTIME, in the tests (`test_hiring_signal_search.py`,
   `test_hiring_signal_service.py`): a transport records every request of a
   whole search over results full of linkedin.com links, and only provider
   hosts appear. Here: a provider that REDIRECTS to linkedin.com is not
   followed.
2. RUNTIME, in the product: the client the routes are handed is not
   `app.state.http` but its own, built with a request hook that refuses every
   host but the four providers (`hiring_signal_search.refuse_non_provider_hosts`).
   The hook is tested where it is defined; here, that the app builds the
   client with it and that the routes take that client.
3. STATIC, on the source (this file): scanners over the feature's modules.
   Outside the network module a module may not call an HTTP verb on anything,
   import a network library, use the client any way but passing it to a
   sanctioned callee (no alias, no attribute access, no `getattr`), reach the
   shared `app.state` client, name linkedin or build a url in code, or import
   anything dynamically. The network module may not hold a url literal, name
   linkedin, or reach past the four `<provider>_search` helpers, and none of
   those helpers' endpoints may be anything but one of the four fixed https
   hosts, used exactly and never built from an argument.
4. SELF-CHECK: the scanners are not trusted because they pass. Every scanner is
   also run over mutated copies of the real sources -- the ways round a naive
   check found in review (an aliased client, `getattr`, a renamed client with a
   url split across two strings, a module-level url, `importlib`, the shared
   `app.state.http`), plus a linkedin url literal, a stray `http.get(...)`,
   `import requests`, a provider endpoint swapped for linkedin.com or for an
   unknown host, a followed redirect -- and must flag each one.

The scanners take SOURCE TEXT, not a path, precisely so that the self-check can
feed them a mutation without touching any real file.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Collection
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
from hiring_signal_fakes import RecordingTransport

from between_jobs.api import hiring_signal_routes, hiring_signal_search
from between_jobs.api.app_state import get_hiring_http_client
from between_jobs.api.errors import ApiError
from between_jobs.api.hiring_signal_search import HiringProvider, search_provider

API_DIR = Path(hiring_signal_search.__file__).parent
NETWORK_MODULE = API_DIR / "hiring_signal_search.py"
PROVIDERS_MODULE = API_DIR / "search_providers.py"
APP_MODULE = API_DIR / "app.py"

FEATURE_MODULES = {
    "hiring_signals.py",
    "hiring_signal_cache.py",
    "hiring_signal_company.py",
    "hiring_signal_query.py",
    "hiring_signal_registry.py",
    "hiring_signal_relevance.py",
    "hiring_signal_routes.py",
    "hiring_signal_saves_store.py",
    "hiring_signal_search.py",
    "hiring_signal_searches_store.py",
    "hiring_signal_service.py",
    "hiring_signal_tab.py",
}
"""Every module of the feature. Listed, and checked against the directory, so a
new module cannot be added without being scanned."""

FIXED_PROVIDER_HOSTS = {
    "ydc-index.io",
    "api.search.brave.com",
    "google.serper.dev",
    "api.firecrawl.dev",
}
HELPER_TO_ENDPOINT = {
    "you_com_search": ("_YOU_COM_URL", "ydc-index.io"),
    "brave_search": ("_BRAVE_URL", "api.search.brave.com"),
    "serper_search": ("_SERPER_URL", "google.serper.dev"),
    "firecrawl_search": ("_FIRECRAWL_URL", "api.firecrawl.dev"),
}
"""The only helpers the network module may call into `search_providers`, and
the one constant and host each of them may use."""
ALLOWED_PROVIDER_IMPORTS = {
    name
    for helper in HELPER_TO_ENDPOINT
    for name in (
        helper,
        helper.removesuffix("_search") + "_entries",
        helper.removesuffix("_search") + "_hit_fields",
    )
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── scanners (source text in, violations out) ────────────────────────────

_NETWORK_IMPORT_ROOTS = {
    "socket",
    "ssl",
    "requests",
    "aiohttp",
    "urllib3",
    "httpcore",
    "websockets",
    "ftplib",
    "smtplib",
    "telnetlib",
    "imaplib",
    "poplib",
    "webbrowser",
}
_NETWORK_IMPORT_MODULES = {"urllib.request", "http.client", "xmlrpc.client"}
_HTTP_VERBS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "request",
    "send",
    "stream",
    "build_request",
}
_CLIENT_NAMES = {"http", "client", "httpx", "session", "transport", "requests"}
_HTTPX_CONSTRUCTORS = {"AsyncClient", "Client", "AsyncHTTPTransport", "HTTPTransport"}


def network_import_violations(source: str) -> list[str]:
    """Imports of a library that opens sockets, other than `httpx` (which the
    feature needs for types and for the one place it is used)."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
        for name in names:
            if name.split(".")[0] in _NETWORK_IMPORT_ROOTS or any(
                name == mod or name.startswith(mod + ".") for mod in _NETWORK_IMPORT_MODULES
            ):
                found.append(f"imports {name}")
    return found


_VERBS_ANYWHERE = _HTTP_VERBS - {"get", "delete"}
"""Verbs nothing else in these modules says. `get` and `delete` are also what a
dict and a Supabase query say, so those two are only flagged on a client-like
name (and a client is followed by `client_flow_violations` wherever it goes)."""


def _decorator_calls(tree: ast.AST) -> set[int]:
    """`@router.post(...)` is a call of a `.post` attribute too, and not one that
    sends anything."""
    return {
        id(decorator)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
    }


def client_call_violations(source: str) -> list[str]:
    """An HTTP verb called on ANYTHING (`.post`, `.send`, `.stream`, ... -- the
    base does not matter, so a renamed client is caught as well as `http`), a
    `get`/`delete` on something named like a client, or an httpx client or
    transport constructed, or a bare `httpx.get(...)`."""
    tree = ast.parse(source)
    decorators = _decorator_calls(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if id(node) in decorators:
            continue
        base = node.func.value.id if isinstance(node.func.value, ast.Name) else None
        attr = node.func.attr
        if attr in _VERBS_ANYWHERE:
            found.append(f"line {node.lineno}: {ast.unparse(node.func.value)}.{attr}(...)")
        elif base in _CLIENT_NAMES and attr in _HTTP_VERBS:
            found.append(f"line {node.lineno}: {base}.{attr}(...)")
        if base == "httpx" and (attr in _HTTPX_CONSTRUCTORS or attr in _HTTP_VERBS):
            found.append(f"line {node.lineno}: httpx.{attr}(...)")
    return found


_CLIENT_LIKE_NAMES = {"http", "client", "session", "transport"}


def _callee_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def client_flow_violations(source: str, *, allowed_callees: Collection[str]) -> list[str]:
    """Where the HTTP client goes. A name that holds one (`http`, `client`,
    `session`, `transport`, or any parameter annotated `httpx.AsyncClient`) may
    be used in exactly one way: passed, as an argument, to a sanctioned callee
    or to a function defined in the same module (which is scanned by the same
    rule). Not aliased (`c = http`), not read from (`http.get`), not handed to
    `getattr`, not passed to anything unlisted -- so no module can grow a way to
    call a url that the verb scan and the string scan would both miss."""
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    local_functions = {
        n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    names = set(_CLIENT_LIKE_NAMES)
    for node in ast.walk(tree):
        # `httpx.AsyncClient` in full: Supabase's own client is also an AsyncClient
        if (
            isinstance(node, ast.arg)
            and node.annotation is not None
            and "httpx.AsyncClient" in ast.unparse(node.annotation)
        ):
            names.add(node.arg)
    found: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in names):
            continue
        parent = parents[node]
        call: ast.AST | None = None
        if isinstance(parent, ast.Call) and node in parent.args:
            call = parent
        elif isinstance(parent, ast.keyword):
            call = parents[parent]
        if not isinstance(call, ast.Call):
            found.append(
                f"line {node.lineno}: client {node.id!r} used as a {type(parent).__name__} "
                "(it may only be passed on)"
            )
            continue
        callee = _callee_name(call)
        if callee not in allowed_callees and callee not in local_functions:
            found.append(f"line {node.lineno}: client {node.id!r} passed to {callee}(...)")
    return found


_DYNAMIC_BUILTINS = {
    "getattr",
    "setattr",
    "delattr",
    "__import__",
    "eval",
    "exec",
    "compile",
    "globals",
    "vars",
}
_DYNAMIC_ATTRIBUTES = frozenset(
    {"__getattribute__", "__getattr__", "__dict__", "attrgetter", "methodcaller"}
)
"""Ways to reach an attribute by a NAME HELD AS A STRING that are not the builtin
`getattr`: the dunder that `getattr` itself calls, the instance dictionary, and the
two `operator` helpers that build a getter from a string."""
_DYNAMIC_MODULES = frozenset({"importlib", "operator"})


def dynamic_access_violations(source: str) -> list[str]:
    """Anything that can reach a name, a module or code without spelling it:
    `getattr` and its siblings, `__import__`, `importlib`, `eval`, `exec`, the
    builtin `compile` (`re.compile` is an attribute, not this), the dunders that
    look an attribute up by a string (`sb.__getattribute__('ta' + 'ble')`), and
    the `operator` module (`operator.attrgetter('table')(sb)`) -- none of which
    spells the name a table scan would read."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _DYNAMIC_BUILTINS
        ):
            found.append(f"line {node.lineno}: {node.func.id}(...)")
        elif isinstance(node, ast.Name) and node.id in _DYNAMIC_MODULES | _DYNAMIC_ATTRIBUTES:
            found.append(f"line {node.lineno}: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in _DYNAMIC_ATTRIBUTES:
            found.append(f"line {node.lineno}: .{node.attr}")
        elif isinstance(node, ast.Import) and any(
            a.name.split(".")[0] in _DYNAMIC_MODULES for a in node.names
        ):
            found.append(f"line {node.lineno}: import of a dynamic-access module")
        elif (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").split(".")[0] in _DYNAMIC_MODULES
        ):
            found.append(f"line {node.lineno}: from a dynamic-access module import ...")
    return found


def state_access_violations(source: str) -> list[str]:
    """`request.app.state.http` is the shared client (the ATS poller's and
    Gmail's too): the feature must be handed its own, never reach for that one.
    Flags `<x>.app.state` and `app.state`; a `.state` on anything else (the
    registry matcher's result has one) is not the app's."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Attribute) and node.attr == "state"):
            continue
        holder = node.value
        if (isinstance(holder, ast.Attribute) and holder.attr == "app") or (
            isinstance(holder, ast.Name) and holder.id == "app"
        ):
            found.append(f"line {node.lineno}: {ast.unparse(node)}")
    return found


def app_state_import_violations(source: str, *, allowed: Collection[str]) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == "app_state":
            found += [
                f"imports {alias.name} from app_state"
                for alias in node.names
                if alias.name not in allowed
            ]
        elif isinstance(node, ast.Import):
            found += [
                f"imports {alias.name}" for alias in node.names if alias.name.endswith("app_state")
            ]
    return found


def _folded_strings(node: ast.AST) -> str | None:
    """The value of a string built from constants: a literal, `'a' + 'b'`, or an
    f-string (its holes read as `{}`). `None` for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _folded_strings(node.left), _folded_strings(node.right)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        return "".join(str(v.value) if isinstance(v, ast.Constant) else "{}" for v in node.values)
    return None


def url_constant_violations(
    source: str, *, forbid_linkedin_word: bool, allowed: Collection[str] = ()
) -> list[str]:
    """A url, or (when `forbid_linkedin_word`) the word linkedin, in any string
    the code builds -- literals, `'a' + 'b'` and f-strings alike -- other than
    the `allowed` ones (the two url TEMPLATES the browser is handed)."""
    tree = ast.parse(source)
    prose = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    found: list[str] = []
    for node in ast.walk(tree):
        if id(node) in prose or not isinstance(node, ast.Constant | ast.BinOp | ast.JoinedStr):
            continue
        text = _folded_strings(node)
        if text is None or text in allowed:
            continue
        if re.search(r"https?://|www\.", text) or (
            forbid_linkedin_word and "linkedin" in text.lower()
        ):
            found.append(f"line {getattr(node, 'lineno', '?')}: {text[:50]!r}")
    return found


SANCTIONED_CALLEES = {"search_provider", "search_application", "search_tab"}
"""Where the feature's modules may hand the client: the one function that owns
the network calls, and the two service entry points the routes hand it to (the
per-application search and the standalone tab's)."""
ROUTES_MAY_IMPORT_FROM_APP_STATE = {"get_hiring_http_client", "get_supabase"}
TEMPLATE_CONSTANTS = {
    "hiring_signals.py": {
        "https://www.linkedin.com/embed/feed/update/urn:li:activity:{activity_id}",
        # the sanitized public post address the parser hands back (its host is
        # validated as `*.linkedin.com`; the pieces are the f-string's holes)
        "https://{}/posts/{}",
        "https://",
    },
    "hiring_signal_saves_store.py": {
        "https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}"
    },
}
"""The only urls this backend ever builds: the embed address, the canonical
post address, and the sanitized post address the parser echoes back. All three
are strings handed to the user's browser; none is ever requested."""
NO_LINKEDIN_WORD_MODULES = {
    "hiring_signal_cache.py",
    "hiring_signal_company.py",
    "hiring_signal_query.py",
    "hiring_signal_registry.py",
    "hiring_signal_routes.py",
    "hiring_signal_saves_store.py",
    "hiring_signal_searches_store.py",
    "hiring_signal_service.py",
    "hiring_signal_tab.py",
}
"""Modules with no business naming LinkedIn at all in code (the parser, the
relevance rules and the network module say it in patterns and prose; the
network module has its own, stricter scan)."""


def feature_module_violations(name: str, source: str) -> list[str]:
    """Every rule for a feature module that is NOT the network module."""
    return (
        network_import_violations(source)
        + client_call_violations(source)
        + client_flow_violations(source, allowed_callees=SANCTIONED_CALLEES)
        + dynamic_access_violations(source)
        + state_access_violations(source)
        + app_state_import_violations(
            source,
            allowed=ROUTES_MAY_IMPORT_FROM_APP_STATE if name == "hiring_signal_routes.py" else (),
        )
        + url_constant_violations(
            source,
            forbid_linkedin_word=name in NO_LINKEDIN_WORD_MODULES,
            allowed=TEMPLATE_CONSTANTS.get(name, ()),
        )
    )


def code_string_constants(source: str) -> list[str]:
    """Every string constant that is CODE -- not a docstring or a bare string
    statement, which is where prose (and this feature's explanations of what
    it does NOT do) lives."""
    tree = ast.parse(source)
    prose = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in prose
    ]


def network_module_violations(source: str) -> list[str]:
    """The network module hands a client to `search_providers`' helpers and
    does nothing else with it: no verb call, no url literal, no linkedin, no
    import from `search_providers` beyond the helpers, no redirect following."""
    found = network_import_violations(source) + client_call_violations(source)
    found += client_flow_violations(source, allowed_callees=set(HELPER_TO_ENDPOINT))
    found += dynamic_access_violations(source)
    found += [
        f"url-like string {s[:40]!r}"
        for s in code_string_constants(source)
        if re.search(r"https?://|www\.", s) or "linkedin" in s.lower()
    ]
    found += [
        f"url-like string {v}" for v in url_constant_violations(source, forbid_linkedin_word=True)
    ]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("search_providers"):
            found += [
                f"imports {alias.name} from search_providers"
                for alias in node.names
                if alias.name not in ALLOWED_PROVIDER_IMPORTS
            ]
        if isinstance(node, ast.keyword) and node.arg == "follow_redirects":
            found.append("passes follow_redirects")
    return found


def endpoint_violations(providers_source: str) -> list[str]:
    """Each of the four `<provider>_search` helpers must (a) exist, (b) use
    exactly ONE `_*_URL` constant, and pass that constant itself -- never a
    string built from its arguments -- as the url of its single HTTP call, (c)
    whose value is an https url on the provider's own fixed host, (d) never
    following redirects."""
    tree = ast.parse(providers_source)
    constants = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
    }
    found: list[str] = []
    for helper, (constant, host) in HELPER_TO_ENDPOINT.items():
        function = functions.get(helper)
        if function is None:
            found.append(f"{helper} is missing")
            continue
        used = {n.id for n in ast.walk(function) if isinstance(n, ast.Name) and "_URL" in n.id}
        if used != {constant}:
            found.append(f"{helper} uses {sorted(used)} instead of only {constant}")
        calls = [
            n
            for n in ast.walk(function)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in {"get", "post"}
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "http"
        ]
        if len(calls) != 1:
            found.append(f"{helper} makes {len(calls)} http calls, expected exactly 1")
        for call in calls:
            first = call.args[0] if call.args else None
            if not (isinstance(first, ast.Name) and first.id == constant):
                found.append(f"{helper} does not pass {constant} itself as the url")
        if any(
            isinstance(n, ast.keyword) and n.arg == "follow_redirects" for n in ast.walk(function)
        ):
            found.append(f"{helper} passes follow_redirects")
        url = constants.get(constant, "")
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != host or "linkedin" in url.lower():
            found.append(f"{constant} = {url!r} is not https://{host}/...")
    hosts = {urlsplit(constants.get(c, "")).hostname for c, _ in HELPER_TO_ENDPOINT.values()}
    if hosts != FIXED_PROVIDER_HOSTS:
        found.append(f"endpoint hosts are {sorted(str(h) for h in hosts)}")
    return found


def shared_client_violations(app_source: str) -> list[str]:
    """The client the routes are handed is built in `app.py`; it must not
    follow redirects (a provider answering 302 -> linkedin.com would otherwise
    become a server-side request to LinkedIn)."""
    found: list[str] = []
    for node in ast.walk(ast.parse(app_source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "AsyncClient"
        ):
            for kw in node.keywords:
                if kw.arg == "follow_redirects" and not (
                    isinstance(kw.value, ast.Constant) and kw.value.value is False
                ):
                    found.append(f"line {node.lineno}: AsyncClient(follow_redirects=...)")
    return found


def hiring_client_violations(app_source: str) -> list[str]:
    """The client the routes are handed (`app.state.hiring_http`) must be built
    with the request hook that refuses every host but the four providers."""
    for node in ast.walk(ast.parse(app_source)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Attribute) and t.attr == "hiring_http" for t in node.targets
        ):
            call = node.value
            if not (isinstance(call, ast.Call) and ast.unparse(call.func) == "httpx.AsyncClient"):
                return ["app.state.hiring_http is not an httpx.AsyncClient(...)"]
            hooks = next((kw.value for kw in call.keywords if kw.arg == "event_hooks"), None)
            if hooks is None:
                return ["app.state.hiring_http has no event_hooks"]
            if "refuse_non_provider_hosts" not in ast.unparse(hooks):
                return ["app.state.hiring_http does not hook refuse_non_provider_hosts"]
            return []
    return ["app.state.hiring_http is never assigned"]


# ── the real sources are clean ───────────────────────────────────────────


def test_every_module_of_the_feature_is_listed_and_therefore_scanned() -> None:
    assert {p.name for p in API_DIR.glob("hiring_signal*.py")} == FEATURE_MODULES


@pytest.mark.parametrize("name", sorted(FEATURE_MODULES - {"hiring_signal_search.py"}))
def test_only_the_network_module_may_touch_a_client(name: str) -> None:
    assert feature_module_violations(name, read(API_DIR / name)) == []


def test_the_network_module_only_delegates_to_the_four_provider_helpers() -> None:
    assert network_module_violations(read(NETWORK_MODULE)) == []


def test_the_scan_is_looking_at_real_code() -> None:
    """Guards against the scanners passing because they saw nothing."""
    source = read(NETWORK_MODULE)
    assert "you_com_search" in source and len(code_string_constants(source)) > 5
    assert len(ALLOWED_PROVIDER_IMPORTS) == 12
    assert "hiring_signal_search.py" in FEATURE_MODULES
    # the client flows through the service and the routes: with no callee
    # sanctioned, the flow scan finds it in both
    for name in ("hiring_signal_service.py", "hiring_signal_routes.py"):
        assert client_flow_violations(read(API_DIR / name), allowed_callees=set()), name
    # ... and the url rule sees the two templates it lets through (with nothing
    # allowed, each of those modules is flagged for its template)
    for name, templates in TEMPLATE_CONSTANTS.items():
        assert url_constant_violations(
            read(API_DIR / name), forbid_linkedin_word=False, allowed=()
        ), f"{name} no longer holds its url template {templates}"


def test_the_four_endpoints_are_fixed_https_hosts_used_exactly() -> None:
    assert endpoint_violations(read(PROVIDERS_MODULE)) == []


def test_the_shared_http_client_does_not_follow_redirects() -> None:
    assert shared_client_violations(read(APP_MODULE)) == []


# ── the self-check, part 1: the ways round a naive scan ──────────────────

_LINKEDIN_FEED = "https://www.linkedin.com/feed/update/urn:li:activity:7506381452083381426"

BYPASSES: list[tuple[str, str]] = [
    # (label, code appended to a feature module)
    (
        "an aliased client",
        f"\n\nasync def _leak(http):\n    c = http\n    await c.get({_LINKEDIN_FEED!r})\n",
    ),
    (
        "getattr on the client",
        f"\n\nasync def _leak(http):\n    await getattr(http, 'get')({_LINKEDIN_FEED!r})\n",
    ),
    (
        "a renamed client and a url split across two strings",
        "\n\nasync def _leak(hiring_http):\n"
        "    await hiring_http.get('https://www.link' + 'edin.com/feed')\n",
    ),
    (
        "a renamed client, an f-string url",
        "\n\nasync def _leak(hiring_http, activity):\n"
        "    await hiring_http.get(f'https://www.linkedin.com/feed/update/{activity}')\n",
    ),
    ("a module-level linkedin url", f"\n\n_PROBE = {_LINKEDIN_FEED!r}\n"),
    ("a module-level url of any host", "\n\n_PROBE = 'https://evil.example/x'\n"),
    ("the bare word in a constant", "\n\n_HOST = 'linkedin.com'\n"),
    ("importlib", "\n\nimport importlib\n\n_m = importlib.import_module('requests')\n"),
    ("importlib used without an import line", "\n\n_m = importlib.import_module('requests')\n"),
    ("__import__", "\n\n_m = __import__('requests')\n"),
    ("eval", "\n\n_x = eval('1')\n"),
    ("setattr", "\n\n_x = setattr(object(), 'a', 1)\n"),
    ("__getattribute__", "\n\n_x = object().__getattribute__('a' + 'b')\n"),
    ("operator.attrgetter", "\n\n_x = operator.attrgetter('get')\n"),
    ("operator, imported", "\n\nimport operator\n"),
    ("attrgetter, imported by name", "\n\nfrom operator import attrgetter\n"),
    (
        "the shared client off app.state",
        "\n\nasync def _leak(request):\n"
        f"    await request.app.state.http.get({_LINKEDIN_FEED!r})\n",
    ),
    (
        "the shared client, url hidden in a variable",
        "\n\nasync def _leak(request, where):\n    return request.app.state.http\n",
    ),
    (
        "the client handed to something unlisted",
        "\n\nasync def _leak(http, other):\n    return await other.fetch(http)\n",
    ),
    (
        "the client returned",
        "\n\ndef _leak(http):\n    return http\n",
    ),
    (
        "the client stored in a container",
        "\n\ndef _leak(http):\n    return [http]\n",
    ),
    ("a verb on an unrelated name", "\n\nasync def _leak(x):\n    await x.post('u')\n"),
    ("a verb `send`", "\n\nasync def _leak(x, r):\n    await x.send(r)\n"),
    ("a stream", "\n\nasync def _leak(x):\n    async with x.stream('GET', 'u'):\n        pass\n"),
    ("a client built here", "\n\ndef _make():\n    return httpx.AsyncClient()\n"),
    ("a bare httpx call", "\n\nasync def _leak(u):\n    await httpx.get(u)\n"),
    ("a socket", "\n\nimport socket\n"),
    ("the shared-client dependency", "\n\nfrom .app_state import get_http_client\n"),
]
"""Each is appended to every non-network feature module and must be flagged
there. The first group is what a review found the previous scanner missed (it
passed every one of these); the rest are the earlier mutations."""


@pytest.mark.parametrize("label", [label for label, _ in BYPASSES])
@pytest.mark.parametrize("name", sorted(FEATURE_MODULES - {"hiring_signal_search.py"}))
def test_a_way_round_the_scan_is_flagged_in_every_feature_module(name: str, label: str) -> None:
    code = dict(BYPASSES)[label]
    source = read(API_DIR / name)
    assert feature_module_violations(name, source) == []
    if label == "the shared-client dependency" and name == "hiring_signal_routes.py":
        pytest.skip("the routes may import from app_state, but only the two guarded names")
    if label == "the bare word in a constant" and name not in NO_LINKEDIN_WORD_MODULES:
        pytest.skip("the parser and the relevance rules say linkedin in patterns, by design")
    assert feature_module_violations(name, source + code), (name, label)


def test_the_routes_may_import_only_the_guarded_client_from_app_state() -> None:
    source = read(API_DIR / "hiring_signal_routes.py")
    mutated = source + "\n\nfrom .app_state import get_http_client\n"
    violations = feature_module_violations("hiring_signal_routes.py", mutated)
    assert any("get_http_client" in v for v in violations)


def test_the_scanners_do_not_flag_what_is_innocent() -> None:
    """A dict's `get`, a route decorator, `re.compile`, a client passed on to the
    sanctioned callee, and prose about linkedin must all stay legal."""
    source = read(API_DIR / "hiring_signal_service.py") + (
        "\n\ndef _fine(row, http, supabase):\n"
        "    value = row.get('k')\n"
        "    return search_provider(http, 'brave', api_key=value, query='q', freshness_param='x')\n"
        '\n\n"""LinkedIn is never requested"""\n'
    )
    assert feature_module_violations("hiring_signal_service.py", source) == []
    routes = read(API_DIR / "hiring_signal_routes.py")
    assert (
        "@router.get" in routes
        and feature_module_violations("hiring_signal_routes.py", routes) == []
    )


def test_the_url_templates_are_the_only_urls_the_backend_builds() -> None:
    for name in TEMPLATE_CONSTANTS:
        source = read(API_DIR / name)
        assert feature_module_violations(name, source) == []
        assert feature_module_violations(name, source + "\n\n_EXTRA = 'https://evil.example/'\n")
        # a template with something appended is a different, unlisted string
        extended = (
            source + "\n\n_EXTRA = 'https://www.linkedin.com/feed/update/urn:li:activity:{}'\n"
        )
        assert feature_module_violations(name, extended), name


# ── the self-check, part 2: the earlier mutations ────────────────────────


def _mutated(source: str, old: str, new: str) -> str:
    assert old in source, old
    return source.replace(old, new, 1)


def test_a_linkedin_url_literal_in_the_network_module_is_flagged() -> None:
    source = read(NETWORK_MODULE)
    mutated = source + f'\n_PROBE = "{_LINKEDIN_FEED}"\n'
    assert network_module_violations(source) == []
    assert any("url-like" in v for v in network_module_violations(mutated))


def test_a_bare_linkedin_word_in_code_is_flagged_even_without_a_url() -> None:
    mutated = read(NETWORK_MODULE) + '\n_HOST = "linkedin"\n'
    assert any("url-like" in v for v in network_module_violations(mutated))


def test_prose_that_mentions_linkedin_is_not_flagged() -> None:
    """The docstrings explain the hard line in words; that must stay legal."""
    source = read(NETWORK_MODULE)
    docstring = ast.get_docstring(ast.parse(source), clean=False)
    assert docstring is not None and "linkedin.com" in docstring
    assert network_module_violations(source) == []


def test_a_direct_http_call_in_the_network_module_is_flagged() -> None:
    mutated = read(NETWORK_MODULE) + (
        "\n\nasync def _fetch_post(http: httpx.AsyncClient, url: str) -> None:\n"
        "    await http.get(url)\n"
    )
    assert any("http.get" in v for v in network_module_violations(mutated))


@pytest.mark.parametrize("name", sorted(FEATURE_MODULES - {"hiring_signal_search.py"}))
def test_a_direct_http_call_in_any_other_module_is_flagged(name: str) -> None:
    source = read(API_DIR / name)
    for call in ("await http.get(url)", "await client.post(url)", "httpx.get(url)"):
        mutated = source + f"\n\nasync def _leak(http, client, url):\n    {call}\n"
        assert client_call_violations(mutated), call
    constructed = source + "\n\ndef _make():\n    return httpx.AsyncClient()\n"
    assert client_call_violations(constructed)


@pytest.mark.parametrize(
    "line",
    [
        "import requests",
        "import socket",
        "import aiohttp",
        "import urllib.request",
        "from urllib import request",
        "from urllib.request import urlopen",
        "from http.client import HTTPSConnection",
        "import ssl",
    ],
)
def test_a_network_library_import_is_flagged(line: str) -> None:
    source = read(API_DIR / "hiring_signal_service.py")
    mutated = _mutated(source, "import asyncio\n", f"import asyncio\n{line}\n")
    assert client_call_violations(mutated) == []
    assert network_import_violations(source) == []
    assert network_import_violations(mutated), line


def test_urllib_parse_is_not_a_network_import() -> None:
    """The pure parser uses `urllib.parse` to split url STRINGS; that must not
    trip the scanner."""
    assert "urllib.parse" in read(API_DIR / "hiring_signals.py")
    assert network_import_violations(read(API_DIR / "hiring_signals.py")) == []


def test_importing_anything_else_from_search_providers_is_flagged() -> None:
    source = read(NETWORK_MODULE)
    for extra in ("fetch_firecrawl", "_FIRECRAWL_URL", "fetch_adzuna"):
        mutated = _mutated(
            source, "    you_com_search,\n)", f"    you_com_search,\n    {extra},\n)"
        )
        assert any(extra in v for v in network_module_violations(mutated)), extra


def test_a_provider_endpoint_swapped_for_linkedin_or_an_unknown_host_is_flagged() -> None:
    source = read(PROVIDERS_MODULE)
    for constant, host in (
        ("_FIRECRAWL_URL", "api.firecrawl.dev"),
        ("_YOU_COM_URL", "ydc-index.io"),
        ("_SERPER_URL", "google.serper.dev"),
        ("_BRAVE_URL", "api.search.brave.com"),
    ):
        original = re.search(rf'^{constant} = "([^"]+)"', source, re.MULTILINE)
        assert original is not None and urlsplit(original.group(1)).hostname == host
        for hostile in (
            "https://www.linkedin.com/feed/",
            "https://evil.example/v1/search",
            "http://" + host + "/v1/search",  # not https
        ):
            mutated = _mutated(source, original.group(0), f'{constant} = "{hostile}"')
            assert endpoint_violations(mutated), (constant, hostile)


def test_a_helper_that_builds_its_url_from_an_argument_is_flagged() -> None:
    source = read(PROVIDERS_MODULE)
    assert endpoint_violations(source) == []
    mutated = _mutated(
        source, "            _FIRECRAWL_URL,\n", "            _FIRECRAWL_URL + query,\n"
    )
    assert any("firecrawl_search" in v for v in endpoint_violations(mutated))


def test_a_helper_that_follows_redirects_is_flagged() -> None:
    source = read(PROVIDERS_MODULE)
    mutated = _mutated(
        source,
        "            _YOU_COM_URL,\n",
        "            _YOU_COM_URL,\n            follow_redirects=True,\n",
    )
    assert any("follow_redirects" in v for v in endpoint_violations(mutated))


def test_a_shared_client_that_follows_redirects_is_flagged() -> None:
    source = read(APP_MODULE)
    mutated = _mutated(
        source,
        "app.state.http = httpx.AsyncClient()",
        "app.state.http = httpx.AsyncClient(follow_redirects=True)",
    )
    assert shared_client_violations(source) == []
    assert shared_client_violations(mutated)


# ── runtime: a redirect to linkedin.com is not followed ──────────────────


@pytest.mark.parametrize("provider", ["you_com", "brave", "serper", "firecrawl"])
async def test_a_provider_redirect_to_linkedin_is_never_followed(
    provider: HiringProvider,
) -> None:
    """A 302 from a provider host to a LinkedIn url ends the call as a
    provider failure. The transport sees exactly ONE request, to the provider,
    and none to linkedin.com. (The client is built the way the app builds it:
    default settings, no redirect following.)"""

    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": _LINKEDIN_FEED}, request=request)

    transport = RecordingTransport(redirect)
    client = httpx.AsyncClient(transport=transport)

    with pytest.raises(ApiError) as caught:
        await search_provider(client, provider, api_key="k", query="q", freshness_param="week")

    assert caught.value.code == "PROVIDER_UNAVAILABLE"
    assert len(transport.requests) == 1
    assert all(host in FIXED_PROVIDER_HOSTS for host in transport.hosts)
    assert not any("linkedin" in str(r.url) for r in transport.requests)


# ── the runtime half: the client the routes get refuses non-provider hosts ─


def test_the_app_builds_the_hiring_client_with_the_host_guard() -> None:
    assert hiring_client_violations(read(APP_MODULE)) == []


def test_the_host_guard_check_flags_each_way_of_losing_it() -> None:
    source = read(APP_MODULE)
    guarded = 'httpx.AsyncClient(event_hooks={"request": [refuse_non_provider_hosts]})'
    assert guarded in source
    for mutated in (
        _mutated(source, guarded, "httpx.AsyncClient()"),
        _mutated(source, guarded, 'httpx.AsyncClient(event_hooks={"request": []})'),
        _mutated(source, "app.state.hiring_http = ", "app.state.hiring_http_unused = "),
        _mutated(source, guarded, "app.state.http"),
    ):
        assert hiring_client_violations(mutated), mutated[:0]


def test_the_search_route_is_handed_the_guarded_client_and_not_the_shared_one() -> None:
    import inspect

    parameter = inspect.signature(hiring_signal_routes.search_hiring_signals).parameters["http"]
    assert parameter.default.dependency is get_hiring_http_client
    assert "get_http_client" not in read(API_DIR / "hiring_signal_routes.py")


def test_the_dependency_returns_the_client_the_app_built_for_this_feature() -> None:
    guarded, shared = httpx.AsyncClient(), httpx.AsyncClient()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(hiring_http=guarded, http=shared))
    )
    assert get_hiring_http_client(request) is guarded  # type: ignore[arg-type]


async def test_a_recording_transport_fails_loudly_on_a_host_it_was_not_told_about() -> None:
    transport = RecordingTransport(
        lambda request: httpx.Response(200, json={}, request=request),
        allowed_hosts=frozenset({"ydc-index.io"}),
    )
    client = httpx.AsyncClient(transport=transport)

    await client.get("https://ydc-index.io/v1/search")
    with pytest.raises(AssertionError, match="unexpected host"):
        await client.get("https://www.linkedin.com/feed/update/urn:li:activity:1")
    assert [r.url.host for r in transport.requests] == ["ydc-index.io"]
