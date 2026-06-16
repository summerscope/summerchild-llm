"""Push the local code defaults for managed variables as new versions.

Run from the agent project root:

    uv run python scripts/push_default_values.py

Pulls current state from Logfire, compares each variable's local default
(declared in `summerchild_agent.managed_vars`) against the server's
latest version. For any variable whose value has changed, pushes a new
LabeledValue, which creates a new version record on the server.

The label name used is `default` — purely as the carrier for version
creation. Logfire's `latest` label is reserved and auto-tracks the
highest version on its own, so the agent (which calls `.get()` without
an explicit label) always resolves through `latest` → newest version.
The `default` label is an inert artifact; nothing references it.

Use this when:
- A managed variable's default has changed in code (e.g., prompt edit,
  bounds tweak) and you want the new value live in the UI.
- The server's `latest` is stale relative to your local default and you
  want to bring them back in sync.

Don't use this when you're testing experimental values via *other*
labels — those are managed in the UI or via a separate flow.

Prerequisites:
- `LOGFIRE_API_KEY` in env (or .env) with `project:write_variables` scope.
- `scripts/push_variables.py` has already run at least once to register
  the variables' schemas on the server.
"""

from __future__ import annotations

import json
import sys

import logfire
from logfire.variables.config import LabeledValue

# Importing managed_vars registers the Variable / TemplateVariable
# declarations so we can read their local defaults.
from summerchild_agent import logfire_config, managed_vars  # noqa: F401


def _local_default_serialized(name: str) -> str | None:
    """Return the JSON-serialized local default for variable `name`, or None
    if the variable isn't locally declared."""
    for v in logfire.variables_get():
        if v.name == name:
            # Pydantic's default machinery exposes the raw default value
            # via the Variable's protected attr; we use the public
            # `to_config()` path which is documented.
            cfg = v.to_config()
            example = cfg.example  # JSON-serialized string of the default
            return example
    return None


def main() -> int:
    logfire_config.configure(service_name="summerchild-agent-push-default-values")

    server_config = logfire.variables_pull_config()
    locally_declared = logfire.variables_get()
    if not locally_declared:
        print("No variables locally declared. Nothing to do.")
        return 0

    changes: list[tuple[str, int, str]] = []  # (name, new_version, serialized_value)

    for v in locally_declared:
        name = v.name
        local_serialized = _local_default_serialized(name)
        if local_serialized is None:
            continue

        server_var = server_config.variables.get(name)
        if server_var is None:
            # Schema not on server yet — push_variables.py should run first.
            print(f"  ! {name}: not registered on server; run push_variables.py first.")
            continue

        latest = server_var.latest_version
        server_serialized = latest.serialized_value if latest is not None else None
        if server_serialized == local_serialized:
            continue  # already in sync

        next_version = (latest.version if latest is not None else 0) + 1
        changes.append((name, next_version, local_serialized))

    if not changes:
        print("All managed-variable `latest` values are already in sync with code. Nothing to push.")
        return 0

    print(f"Will push {len(changes)} new version(s) via the `default` label carrier:")
    for name, version, _val in changes:
        print(f"  - {name} -> v{version}")
    confirm = input("Apply? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return 1

    # Apply changes to the pulled config and push back in merge mode so
    # we only touch the variables we're updating. Use the `default` label
    # as the carrier — `latest` is server-reserved and would 400.
    for name, version, serialized_value in changes:
        server_config.variables[name].labels["default"] = LabeledValue(
            version=version,
            serialized_value=serialized_value,
        )

    pushed = logfire.variables_push_config(
        server_config, mode="merge", yes=True
    )
    if pushed:
        print(f"Pushed {len(changes)} new version(s).")
        return 0
    print("Push returned False — check Logfire response.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
