"""One-off script to register all locally-declared managed variables in
Logfire so they appear in the project UI for tuning.

Run from the agent project root:

    uv run python scripts/push_variables.py

You'll be prompted to confirm before any writes. Re-run any time after
adding new `logfire.var(...)` / `logfire.template_var(...)` declarations
in the codebase.

Prerequisites:
- `logfire auth` has run and you can see this dir's `.logfire/`
  credentials (`uv run logfire whoami` confirms).
- The Logfire user has write access to the project's managed-variables
  surface (org-admin or workspace-write in most setups).
"""

from __future__ import annotations

import sys

import logfire

# Importing managed_vars is enough — its module-level `logfire.var(...)`
# call registers the Variable in Logfire's local registry. Anything else
# we declare later (model id, bounds, etc.) will land here automatically
# as long as it's a top-level declaration in an imported module.
from summerchild_agent import logfire_config, managed_vars  # noqa: F401


def main() -> int:
    logfire_config.configure(service_name="summerchild-agent-push-variables")

    locally_declared = logfire.variables_get()
    if not locally_declared:
        print("No variables declared in the imported modules. Nothing to push.")
        return 0

    print(f"Found {len(locally_declared)} locally-declared variable(s):")
    for v in locally_declared:
        print(f"  - {v.name}")
    print()

    # `yes=False` makes this interactive — Logfire will print a diff vs the
    # backend and ask for confirmation.
    pushed = logfire.variables_push(strict=True)
    return 0 if pushed else 1


if __name__ == "__main__":
    sys.exit(main())
