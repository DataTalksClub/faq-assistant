"""Opik tracing for faq-assistant — Lambda-safe, stdlib only.

The Lambda runtime stays zero-dependency (only zerosearch): this module
never imports opik at top level. When opik is installed and OPIK_ENABLED
is not false, the @track decorator below delegates to opik.track;
otherwise it is a transparent no-op.

Local Opik (docker compose in ../opik):
  OPIK_URL_OVERRIDE=http://localhost:5173/api
  OPIK_WORKSPACE=default
  OPIK_PROJECT_NAME=faq-assistant

Disable:
  OPIK_ENABLED=false

opik itself lives in the `test` dependency group, never in runtime
dependencies, so `sam build` bundles stay ~8 MB.
"""

from __future__ import annotations

import os


def is_opik_enabled() -> bool:
    return os.environ.get("OPIK_ENABLED", "true").lower() not in ("0", "false", "no", "off")


def project_name(default: str = "faq-assistant") -> str:
    return os.environ.get("OPIK_PROJECT_NAME", default)


def configure_opik() -> None:
    if not is_opik_enabled():
        return
    try:
        import opik  # type: ignore
    except ImportError:
        return
    try:
        if os.environ.get("OPIK_URL_OVERRIDE"):
            opik.configure(
                url_override=os.environ["OPIK_URL_OVERRIDE"],
                workspace=os.environ.get("OPIK_WORKSPACE", "default"),
            )
        elif not os.path.exists(os.path.expanduser("~/.opik.config")):
            opik.configure(use_local=True)
    except Exception:
        pass


def track(_fn=None, *, project: str | None = None, **kwargs):
    """Lambda-safe @track: real Opik span when available, else no-op."""

    def decorator(fn):
        if not is_opik_enabled():
            return fn
        try:
            from opik import track as opik_track  # type: ignore

            configure_opik()
            return opik_track(project_name=project or project_name(), **kwargs)(fn)
        except Exception:
            return fn

    if _fn is None:
        return decorator
    return decorator(_fn)


def flush() -> None:
    if not is_opik_enabled():
        return
    try:
        import opik  # type: ignore

        opik.flush_tracker()
    except Exception:
        pass
