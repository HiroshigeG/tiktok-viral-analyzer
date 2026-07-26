#!/usr/bin/env python3
"""
brandconfig.py  --  the one place the pipeline learns which brand it is looking at.

Everything brand specific lives in a JSON config plus a brand brief written in
markdown. No module hardcodes a handle, a hashtag, or a line of brand voice.
Point the pipeline at a different config and it analyzes a different category.

    from brandconfig import load_config
    cfg = load_config()                      # config/brand.json
    cfg = load_config("config/nike.json")    # anything else

The config carries:

    subject      the brand being advised. Its own posts become the "what the
                 brand does today" half of the gap analysis, so this is not
                 just another entry in the list.
    competitors  the comparison set. Two is the tested shape (closest
                 challenger plus category anchor) but any number works.
    brief        path to the markdown brief. This is the rubric every
                 brand_fit score answers to, so a stale brief silently
                 produces confident, meaningless numbers.
    generation   optional. Strings make_prompt.py needs for the MAKE step.

Brand voice rule: never a hyphen or em dash as a generic connector in any
string that reaches the rendered site.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG_PATH = "config/brand.json"

# Env override so a run can be retargeted without editing a command line
# buried in a script.
CONFIG_ENV_VAR = "SIGNAL_BRAND_CONFIG"

_REQUIRED_BRAND_KEYS = ("key", "name", "handle", "hashtag")


class BrandConfigError(Exception):
    """Raised when a config is missing, malformed, or internally inconsistent."""


class Brand:
    """One brand in the comparison set."""

    def __init__(self, raw: Dict[str, Any], role: str) -> None:
        missing = [k for k in _REQUIRED_BRAND_KEYS if not str(raw.get(k, "")).strip()]
        if missing:
            raise BrandConfigError(
                "{} entry is missing required field(s): {}".format(
                    role, ", ".join(missing)
                )
            )
        self.key: str = str(raw["key"]).strip().lower()
        self.name: str = str(raw["name"]).strip()
        self.handle: str = str(raw["handle"]).strip()
        self.hashtag: str = str(raw["hashtag"]).strip().lstrip("#")
        self.role: str = role

    @property
    def is_subject(self) -> bool:
        return self.role == "subject"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<Brand {} ({}) {}>".format(self.key, self.role, self.handle)


class BrandConfig:
    """Resolved configuration for one pipeline run."""

    def __init__(self, raw: Dict[str, Any], source_path: Path) -> None:
        self.source_path = source_path

        if "subject" not in raw:
            raise BrandConfigError(
                "config has no 'subject'. The subject is the brand being advised; "
                "its own posts become the baseline the gap analysis compares against."
            )

        self.subject = Brand(raw["subject"], "subject")

        competitors_raw = raw.get("competitors") or []
        if not isinstance(competitors_raw, list):
            raise BrandConfigError("'competitors' must be a list")
        self.competitors: List[Brand] = [
            Brand(c, "competitor") for c in competitors_raw
        ]

        self.brands: List[Brand] = [self.subject] + self.competitors

        keys = [b.key for b in self.brands]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise BrandConfigError(
                "duplicate brand key(s): {}. Keys name the data files, so they "
                "must be unique.".format(", ".join(sorted(dupes)))
            )

        brief_rel = str(raw.get("brief", "")).strip()
        if not brief_rel:
            raise BrandConfigError(
                "config has no 'brief'. The brief is the scoring rubric; without "
                "it every brand_fit score is arbitrary."
            )
        self.brief_path = (source_path.parent.parent / brief_rel).resolve()
        if not self.brief_path.is_file():
            # Also accept a path relative to the current working directory, which
            # is what a user typing a path by hand will naturally produce.
            alt = Path(brief_rel).resolve()
            if alt.is_file():
                self.brief_path = alt
            else:
                raise BrandConfigError(
                    "brief not found at '{}'. Expected a markdown file.".format(brief_rel)
                )

        self.generation: Dict[str, str] = dict(raw.get("generation") or {})

    # ── Convenience accessors ────────────────────────────────────────────

    @property
    def keys(self) -> List[str]:
        """Brand keys in order, subject first. Used as data file stems."""
        return [b.key for b in self.brands]

    @property
    def handles(self) -> Dict[str, str]:
        """{brand key: official handle} for the profile scrape."""
        return {b.key: b.handle for b in self.brands}

    @property
    def hashtags(self) -> Dict[str, str]:
        """{brand key: hashtag} for the owner and creator scrape."""
        return {b.key: b.hashtag for b in self.brands}

    @property
    def names(self) -> Dict[str, str]:
        """{brand key: display name} for anything that renders."""
        return {b.key: b.name for b in self.brands}

    def name_for(self, key: str) -> str:
        """Display name for a brand key, falling back to a titled key."""
        return self.names.get(str(key).lower(), str(key).title())

    def brief_text(self) -> str:
        """The full brief markdown. The rubric behind every brand_fit score."""
        return self.brief_path.read_text(encoding="utf-8")

    def brief_summary(self, max_chars: int = 2000) -> str:
        """
        A compact brief for prompt injection. Strips markdown headings and
        blockquotes so the model reads prose rather than document furniture,
        then truncates on a paragraph boundary.
        """
        lines: List[str] = []
        for line in self.brief_text().splitlines():
            stripped = line.strip()
            if stripped.startswith(">") or stripped.startswith("#"):
                continue
            lines.append(line)
        text = "\n".join(lines).strip()
        while "\n\n\n" in text:
            text = text.replace("\n\n\n", "\n\n")
        if len(text) <= max_chars:
            return text
        cut = text[:max_chars]
        boundary = cut.rfind("\n\n")
        return (cut[:boundary] if boundary > 0 else cut).strip()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<BrandConfig subject={} competitors={}>".format(
            self.subject.key, [c.key for c in self.competitors]
        )


def load_config(path: Optional[str] = None) -> BrandConfig:
    """
    Load the brand config. Resolution order: explicit path, then the
    SIGNAL_BRAND_CONFIG env var, then config/brand.json.

    Raises BrandConfigError with an actionable message rather than a traceback,
    because the most common failure is a fresh checkout with no config yet.
    """
    raw_path = path or os.getenv(CONFIG_ENV_VAR) or DEFAULT_CONFIG_PATH
    config_path = Path(raw_path)

    if not config_path.is_file():
        raise BrandConfigError(
            "no brand config at '{}'.\n"
            "Copy config/TEMPLATE.json to config/brand.json, fill in the brand "
            "and its competitors, and write the brief it points at. "
            "config/lexus.json is a worked example.".format(config_path)
        )

    try:
        with config_path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
    except json.JSONDecodeError as exc:
        raise BrandConfigError("'{}' is not valid JSON: {}".format(config_path, exc))

    return BrandConfig(raw, config_path.resolve())


def add_config_argument(parser: Any) -> None:
    """Attach the shared --config flag so every entry point spells it the same."""
    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help=(
            "Brand config JSON (default: ${} or {}).".format(
                CONFIG_ENV_VAR, DEFAULT_CONFIG_PATH
            )
        ),
    )
