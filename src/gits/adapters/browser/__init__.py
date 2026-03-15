"""Browser adapter — OpenClaw CLI wrapper."""

from .openclaw import (
    ActionResult,
    ElementRef,
    NavResult,
    OpenClawError,
    click,
    evaluate,
    extract_text,
    list_profiles,
    navigate,
    snapshot,
    type_text,
)

__all__ = [
    "ActionResult",
    "ElementRef",
    "NavResult",
    "OpenClawError",
    "click",
    "evaluate",
    "extract_text",
    "list_profiles",
    "navigate",
    "snapshot",
    "type_text",
]
