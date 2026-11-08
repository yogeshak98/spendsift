"""The five demo personas, from ₹10L to ₹3Cr net worth, keyed by slug."""

from __future__ import annotations

from scripts.personas.catalog import p1cr, p3cr, p10l, p30l, p50l
from scripts.personas.model import Persona

ALL: dict[str, Persona] = {
    p.slug: p
    for p in (p10l.PERSONA, p30l.PERSONA, p50l.PERSONA, p1cr.PERSONA, p3cr.PERSONA)
}
