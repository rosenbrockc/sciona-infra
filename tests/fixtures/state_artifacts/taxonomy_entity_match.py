"""Tiny deterministic taxonomy matcher for the stateful NLP vertical slice."""

from __future__ import annotations

from typing import Any, Iterable


def taxonomy_entity_match(text: str, taxonomy: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return Sciona-standard entity spans for exact taxonomy phrase matches."""

    normalized_text = text.casefold()
    matches: list[dict[str, Any]] = []
    for entry in taxonomy:
        entity_type = str(entry.get("entity_type") or entry.get("label") or "Entity")
        terms = [str(entry.get("label") or ""), str(entry.get("name") or "")]
        terms.extend(str(alias) for alias in entry.get("aliases", []))
        for term in terms:
            if not term:
                continue
            start = normalized_text.find(term.casefold())
            if start < 0:
                continue
            matches.append(
                {
                    "entity_type": entity_type,
                    "confidence": 1.0,
                    "span_start": start,
                    "span_end": start + len(term),
                }
            )
    return sorted(matches, key=lambda item: (item["span_start"], item["span_end"]))
