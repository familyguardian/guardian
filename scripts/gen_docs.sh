#!/bin/bash
set -e

# Installiere alle Unterprojekte als editable Pakete ins aktuelle venv
pip install -e guardian_daemon
pip install -e guardian_agent
pip install -e guardian_hub
pip install -e guardianctl

# Baue die zentrale Sphinx-Dokumentation
sphinx-build -b html docs docs/_build/
