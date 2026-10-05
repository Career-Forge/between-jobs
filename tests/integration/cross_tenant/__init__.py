"""Cross-tenant isolation suite (launch plan P4.7): the case table and its harness.

`harness.py` is the machinery; `seeds_core.py` seeds the rows nearly every case needs; each
`cases_*.py` module adds the seeders and `CASES` for one slice of the API. The tests that
run them are `tests/integration/test_local_cross_tenant_routes.py` (every route, as a second
user, through the real app and a real local Supabase stack) and
`tests/integration/test_local_rls_isolation.py` (the same data read straight through
PostgREST with each user's own JWT, and the policies themselves)."""
