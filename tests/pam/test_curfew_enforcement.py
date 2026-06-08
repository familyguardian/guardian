"""L3 (real): assert pam_time.so actually allows/denies at curfew boundaries.

guardian's generated rules are written to the real /etc/security/time.conf and
pamtester evaluates the PAM account phase via faketime at specific moments. This
is the regression test for "curfew does not actually block login".
"""

import datetime

import pytest

pytestmark = pytest.mark.pam

# Fixed reference dates (asserted so a wrong constant fails loudly rather than
# silently testing the wrong weekday). All times treated as UTC by the fixture.
MONDAY = datetime.date(2026, 6, 8)
SATURDAY = datetime.date(2026, 6, 13)


def setup_module(module):
    assert MONDAY.weekday() == 0, "reference MONDAY is not a Monday"
    assert SATURDAY.weekday() == 5, "reference SATURDAY is not a Saturday"


FULL_CURFEW = {
    "curfew": {
        "weekdays": "08:00-20:00",
        "saturday": "09:00-22:00",
        "sunday": "09:00-20:00",
    }
}


def _rules(make_user_manager):
    return make_user_manager(users={"kid1": FULL_CURFEW})._generate_rules()


def test_allowed_during_weekday_window(make_user_manager, pam_curfew):
    allowed, proc = pam_curfew("kid1", _rules(make_user_manager), f"{MONDAY} 14:00:00")
    assert (
        allowed
    ), f"expected login allowed at Mon 14:00, got: {proc.stderr or proc.stdout}"


def test_denied_after_weekday_curfew(make_user_manager, pam_curfew):
    allowed, proc = pam_curfew("kid1", _rules(make_user_manager), f"{MONDAY} 22:00:00")
    assert not allowed, "expected login denied at Mon 22:00 (past 20:00 curfew)"


def test_denied_before_weekday_window(make_user_manager, pam_curfew):
    allowed, _ = pam_curfew("kid1", _rules(make_user_manager), f"{MONDAY} 06:00:00")
    assert not allowed, "expected login denied at Mon 06:00 (before 08:00)"


def test_allowed_during_weekend_window(make_user_manager, pam_curfew):
    allowed, proc = pam_curfew(
        "kid1", _rules(make_user_manager), f"{SATURDAY} 21:00:00"
    )
    assert (
        allowed
    ), f"expected login allowed at Sat 21:00, got: {proc.stderr or proc.stdout}"


def test_denied_after_weekend_window(make_user_manager, pam_curfew):
    allowed, _ = pam_curfew("kid1", _rules(make_user_manager), f"{SATURDAY} 23:30:00")
    assert not allowed, "expected login denied at Sat 23:30 (past 22:00 curfew)"


def test_non_kid_user_always_allowed(make_user_manager, pam_curfew):
    # 'alice' is not managed: the !@kids catch-all should allow her at any time.
    allowed, proc = pam_curfew("alice", _rules(make_user_manager), f"{MONDAY} 23:59:00")
    assert (
        allowed
    ), f"expected non-managed user always allowed, got: {proc.stderr or proc.stdout}"


def test_overnight_curfew_enforced(make_user_manager, pam_curfew):
    """Overnight window (allow 22:00-06:00, deny during the day) is enforced
    across midnight.

    _generate_rules() splits the wrapping window into Wk2200-2400 | Wk0000-0600
    so pam_time evaluates both the pre- and post-midnight halves. Tested on a
    Monday, which is a weekday on both sides of midnight.
    """
    rules = make_user_manager(
        users={"kid1": {"curfew": {"weekdays": "22:00-06:00"}}}
    )._generate_rules()

    # Midday is outside the overnight window -> login denied.
    allowed_midday, _ = pam_curfew("kid1", rules, f"{MONDAY} 14:00:00")
    assert (
        not allowed_midday
    ), "expected login denied at Mon 14:00 (outside 22:00-06:00)"

    # Late evening, before midnight, is inside the window -> login allowed.
    allowed_evening, proc = pam_curfew("kid1", rules, f"{MONDAY} 23:00:00")
    assert (
        allowed_evening
    ), f"expected login allowed at Mon 23:00, got: {proc.stderr or proc.stdout}"

    # Early morning, after midnight, is inside the window -> login allowed.
    allowed_morning, proc = pam_curfew("kid1", rules, f"{MONDAY} 02:00:00")
    assert (
        allowed_morning
    ), f"expected login allowed at Mon 02:00, got: {proc.stderr or proc.stdout}"
