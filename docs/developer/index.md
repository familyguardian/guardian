# Guardian Developer Guide

Welcome to the Guardian developer documentation! This guide explains how the project is developed, what is expected from
contributors, and how you can get started contributing to Guardian.

---

## Contribution Guidelines

- **Code style:** Follow PEP8 and use type hints where possible. All code should be well-documented with clear docstrings
  in English.
- **Pre-commit hooks:** You must install lefthook to ensure code quality and formatting. See below for setup instructions.
- **Tests:** All new features and bugfixes should include relevant tests.
- **Documentation:** Update or add documentation for any new modules, features, or CLI commands.
- **Pull requests:** Make sure your branch is up to date with `main` and all checks pass before submitting a PR.

---

## Project Setup & Workflow

Guardian uses [uv](https://github.com/astral-sh/uv) for Python environment and dependency management. Each subproject
has its own `pyproject.toml` and isolated virtualenv.

### Basic uv Commands

Create a virtual environment for a subproject:

```sh
cd guardian_daemon
uv venv
```

Install dependencies:

```sh
uv pip install -r requirements.txt
# or
uv pip install .
```

Upgrade dependencies:

```sh
uv pip upgrade
```

Run scripts:

```sh
uv run main.py
```

---

## Lefthook Setup

Guardian uses [lefthook](https://github.com/evilmartians/lefthook) for git pre-commit hooks (linting, formatting, etc.).

Install lefthook hooks for your repo:

```sh
uv run lefthook install
```

This ensures all code is checked before commits and PRs.

---

## Building Documentation with MkDocs

Guardian uses [MkDocs](https://www.mkdocs.org/) for user and developer documentation.

To build and serve the docs locally:

```sh
uv pip install mkdocs
mkdocs serve
```

The documentation will be available at <http://localhost:8000>

---

## How to Contribute

1. Fork the repository and clone your fork.
2. Create a new branch for your feature or fix.
3. Set up your environment with uv and lefthook as described above.
4. Make your changes, add tests and documentation.
5. Run all checks and make sure everything passes.
6. Submit a pull request with a clear description of your changes.

---

## Community & Support

If you have questions or want to discuss ideas, open an issue or join our community chat (see the main README for links).

Thank you for helping make Guardian better for families everywhere!
