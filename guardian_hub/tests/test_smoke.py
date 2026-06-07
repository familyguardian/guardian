"""Smoke tests for guardian_hub.

guardian_hub is a Phase-1 stub. These tests wire it into CI/coverage now so the
package stays importable and regressions are caught the moment it is implemented.
"""

import importlib

import pytest


def test_package_imports():
    assert importlib.import_module("guardian_hub") is not None


@pytest.mark.parametrize(
    "name",
    [
        "guardian_hub.api",
        "guardian_hub.db",
        "guardian_hub.models",
        "guardian_hub.main",
    ],
)
def test_submodules_import(name):
    assert importlib.import_module(name) is not None


def test_main_entrypoint_is_callable():
    from guardian_hub import main

    assert callable(main.main)


@pytest.mark.xfail(
    reason="guardian_hub central server (FastAPI app) is not implemented yet (Phase 1)",
    strict=False,
)
def test_api_exposes_app():
    from guardian_hub import api

    assert hasattr(api, "app")
