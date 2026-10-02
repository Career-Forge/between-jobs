import pytest

pytestmark = pytest.mark.local_supabase


def test_red_integration():
    assert False, "deliberate break to prove the integration job goes red"
