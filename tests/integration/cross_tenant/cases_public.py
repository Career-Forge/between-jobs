"""Operations that have no cross-tenant case, and why.

An operation may skip the case only if it fits one of four categories (see
`harness.NotUserScoped`): "public" (no user data at all), "secret" (authenticated by something
other than a user's token), "shared" (reference data every user may read by design) or
"own-identity" (it only ever acts on the caller's own identity and takes no id of anyone
else's). A "public" or "own-identity" operation may not take a path id; the meta-test
enforces that, so a route that gained an id parameter cannot stay excused by accident."""

from __future__ import annotations

from .harness import NotUserScoped

NOT_USER_SCOPED: dict[str, NotUserScoped] = {
    "GET /health": NotUserScoped("public", "liveness and worker status, no user data"),
}
