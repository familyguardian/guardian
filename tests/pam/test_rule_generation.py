"""L3 (fast): golden-string tests for UserManager._generate_rules().

Pure tests - no PAM needed - pinning the exact pam_time.so syntax guardian emits.
Defaults are passed empty so each user's rule reflects only its own curfew
(get_user_policy merges per-day defaults into a user's settings otherwise).
"""

import pytest

pytestmark = pytest.mark.pam

NO_DEFAULT_CURFEW = {"curfew": {}}


def _kid_rule(rules, username):
    return next(r for r in rules if r.startswith(f"*;*;{username};"))


def test_weekday_rule(make_user_manager):
    um = make_user_manager(
        users={"kid1": {"curfew": {"weekdays": "08:00-20:00"}}},
        defaults=NO_DEFAULT_CURFEW,
    )
    rules = um._generate_rules()
    # "08:00-20:00" on weekdays must convert to the pam_time code Wk0800-2000.
    # (Sensible Sat/Sun defaults are injected by the config layer regardless, so
    # assert membership rather than the exact day set.)
    assert "Wk0800-2000" in _kid_rule(rules, "kid1").split(";")[-1].split("|")
    # Non-kids catch-all must always be present and last.
    assert rules[-1] == "*;*;!@kids;Al0000-2400"


def test_combined_days_use_or(make_user_manager):
    um = make_user_manager(
        users={
            "kid1": {
                "curfew": {
                    "weekdays": "08:00-20:00",
                    "saturday": "09:00-22:00",
                    "sunday": "09:00-20:00",
                }
            }
        },
        defaults=NO_DEFAULT_CURFEW,
    )
    parts = set(_kid_rule(um._generate_rules(), "kid1").split(";")[-1].split("|"))
    assert parts == {"Wk0800-2000", "Sa0900-2200", "Su0900-2000"}


def test_multiple_kids_each_get_a_rule(make_user_manager):
    um = make_user_manager(
        users={
            "kid1": {"curfew": {"weekdays": "08:00-20:00"}},
            "kid2": {"curfew": {"weekdays": "07:30-19:30"}},
        },
        defaults=NO_DEFAULT_CURFEW,
    )
    rules = um._generate_rules()
    assert "Wk0800-2000" in _kid_rule(rules, "kid1").split(";")[-1].split("|")
    assert "Wk0730-1930" in _kid_rule(rules, "kid2").split(";")[-1].split("|")


def test_overnight_curfew_splits_across_midnight(make_user_manager):
    """An overnight window (22:00-06:00) wraps past midnight, which pam_time does
    not handle for a single start>end range. _generate_rules() therefore splits
    it into two ranges meeting at midnight (Wk2200-2400 | Wk0000-0600) so
    pam_time enforces both halves. The PAM-level behaviour is asserted in
    test_curfew_enforcement.py::test_overnight_curfew_enforced.
    """
    um = make_user_manager(
        users={"kid1": {"curfew": {"weekdays": "22:00-06:00"}}},
        defaults=NO_DEFAULT_CURFEW,
    )
    parts = _kid_rule(um._generate_rules(), "kid1").split(";")[-1].split("|")
    assert "Wk2200-2400" in parts
    assert "Wk0000-0600" in parts
    # The naive (non-wrapping) start>end range must not be emitted.
    assert "Wk2200-0600" not in parts
