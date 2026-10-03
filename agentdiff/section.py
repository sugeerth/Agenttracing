"""The envelope every section — and every part of a section — is wrapped in.

    {"version": int, "measurable": bool, "reason": str | None, ...}

:func:`measurable` and :func:`unmeasurable` build it; nothing hand-writes
the three leading keys. The order is fixed (version, measurable, reason,
then the payload) so that a reader, a page block or a diff sees the same
shape at the top of every section, and so that two sections built here
serialise identically key for key.

A *part* of a section — a sub-result the section carries under its own
key — has no version of its own: pass ``version=None`` and the key is
left out, giving ``{"measurable", "reason", ...}``. The section still
carries its version at the top; the part inherits it.

Honesty rule: ``unmeasurable`` takes a reason, always. A thing that
cannot be measured says why; it never returns a number that was not
counted.
"""

from __future__ import annotations

from typing import Optional


def _envelope(version: Optional[int], measurable_: bool, reason: Optional[str]) -> dict:
    out: dict = {}
    if version is not None:
        out["version"] = version
    out["measurable"] = measurable_
    out["reason"] = reason
    return out


def measurable(payload: Optional[dict] = None, *, version: Optional[int] = None, **extra) -> dict:
    """The envelope of a section that measured something: ``version``
    (omitted when None), ``measurable: True``, ``reason: None``, then the
    keys of ``payload`` and of ``extra`` in the order given."""
    out = _envelope(version, True, None)
    if payload:
        out.update(payload)
    if extra:
        out.update(extra)
    return out


def unmeasurable(reason: str, *, version: Optional[int] = None, **extra) -> dict:
    """The envelope of a section that could not measure: ``version``
    (omitted when None), ``measurable: False``, the ``reason`` — never
    empty — then the keys of ``extra`` in the order given, which is where
    a section puts the empty shape a reader still expects (``"rows": []``,
    ``"narrative": "..."``)."""
    if not reason:
        raise ValueError("an unmeasurable section must say why")
    out = _envelope(version, False, str(reason))
    if extra:
        out.update(extra)
    return out


def is_measurable(block: object) -> bool:
    """True when ``block`` is a section that measured something."""
    return isinstance(block, dict) and block.get("measurable") is True


__all__ = ["measurable", "unmeasurable", "is_measurable"]
