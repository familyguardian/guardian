# Dependency Management Workflow

This document outlines the process for adding, updating, or removing dependencies in Guardian projects.

## Overview

Guardian uses UV for Python package management and dependency resolution. We use lock files (`uv.lock`)
to ensure deterministic builds and consistent deployments.

## Adding or Updating Dependencies

When adding or modifying dependencies in any of the Guardian components:

1. Update the `pyproject.toml` file of the relevant component with the new/updated dependency.

   ```bash
    cd path/to/guardian_project_root/component/
    uv add new-package
   ```

2. Run `uv sync` in the project root directory to update the lock file:

   ```bash
   cd path/to/guardian_project_root/
   uv sync --all-packages --all-groups
   ```

3. Make sure to explicitly add the updated lock file to Git:

   ```bash
   git add path/to/guardian_project_root/component/uv.lock
   ```

4. Commit and push your changes with a descriptive message:

   ```bash
   git commit -m "feat: add new-package dependency for feature X"
   git push
   ```

5. Deploy using the deploy_and_test.sh script:

   ```bash
   ./scripts/deploy_and_test.sh
   ```

## Important Notes

- **Always commit the lock file**: The `uv.lock` file ensures that the exact same package versions are used in all environments.
- **Use `--frozen` in production**: Our deployment scripts use the `--frozen` flag to install dependencies
  exactly as specified in the lock file.
- **Testing locally**: When developing, you can use `uv run` without the `--frozen` flag for flexibility,
  but always test with `--frozen` before deploying.

## Troubleshooting

If you encounter issues with dependencies not being available:

1. Verify that the dependency is correctly added to `pyproject.toml`
2. Check that you've run `uv sync` and committed the updated lock file
3. If the dependency is still not found, try running without the `--frozen` flag for debugging
