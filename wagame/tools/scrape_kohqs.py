"""Scrape kohqs.com/wa/heroes into wagame/data/heroes.json.

Run from the project root on a host with internet access:

    python -m wagame.tools.scrape_kohqs

By default this writes to wagame/data/heroes.json (overwriting it). Use --dry
to print to stdout without writing.

Stdlib-only — no extra deps. The page is server-rendered HTML; we walk it with
html.parser. The DOM exact structure may need tweaking once the site changes;
when in doubt, run with --raw to dump the raw HTML and inspect manually.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

URL = "https://kohqs.com/wa/heroes"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
OUTPUT = Path(__file__).resolve().parent.parent / "data" / "heroes.json"

log = logging.getLogger("scrape_kohqs")


def fetch_html(url: str = URL) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code} fetching {url}: {exc.reason}") from exc


class _HeroBlockParser(HTMLParser):
    """Best-effort hero extractor.

    The kohqs hero page uses a card-per-hero layout. Each card has a header with
    the hero name and a slug, plus tag pills (rarity, terrain, attribute) and a
    bullet list of bonuses. We collect text per likely card boundary and let
    `_extract_heroes` post-process.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[list[str]] = []
        self._current: list[str] | None = None
        self._depth = 0
        self._card_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = {k: (v or "") for k, v in attrs}
        klass = attrs_d.get("class", "")
        # heuristic: a "hero" / "card" / "item" wrapper opens a card
        if self._current is None and any(k in klass for k in ("hero", "card", "item")):
            self._current = []
            self._card_depth = self._depth
        self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        self._depth -= 1
        if self._current is not None and self._depth <= self._card_depth:
            text = [t for t in (s.strip() for s in self._current) if t]
            if len(text) >= 3:
                self.cards.append(text)
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.append(data)


_RARITY_RE = re.compile(r"\b(common|rare|epic|legendary|mythic)\b", re.IGNORECASE)
_TERRAIN_RE = re.compile(r"\b(forest|plains|highlands|hexlands)\b", re.IGNORECASE)
_DATE_RE = re.compile(r"([a-z]{3})\s+'?(\d{2,4})", re.IGNORECASE)
_NAME_RE = re.compile(r"^[A-Z][A-Za-z' .-]+$")


def _extract_heroes(cards: list[list[str]]) -> list[dict[str, Any]]:
    heroes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tokens in cards:
        name = next((t for t in tokens if _NAME_RE.match(t) and " " in t), None)
        if not name:
            continue
        joined = " ".join(tokens).lower()

        rarity_m = _RARITY_RE.search(joined)
        terrain_m = _TERRAIN_RE.search(joined)
        date_m = _DATE_RE.search(joined)

        rarity = rarity_m.group(1).lower() if rarity_m else "rare"
        terrain = terrain_m.group(1).lower() if terrain_m else None
        release = (
            f"20{date_m.group(2)}-{_month_num(date_m.group(1))}"
            if date_m and len(date_m.group(2)) == 2
            else (date_m.group(0) if date_m else None)
        )

        codename = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        if codename in seen:
            continue
        seen.add(codename)

        bullets = [t for t in tokens if t.endswith("Bonus") or "Bonus" in t]

        heroes.append(
            {
                "codename": codename,
                "name": name,
                "rarity": rarity,
                "element": None,
                "house": None,
                "terrain": terrain,
                "release_date": release,
                "bonuses": bullets,
                "tags": [],
            }
        )
    return heroes


def _month_num(token: str) -> str:
    months = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
        "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }
    return months.get(token[:3].lower(), "01")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=URL, help="Source URL.")
    parser.add_argument("--out", default=str(OUTPUT), help="Output JSON path.")
    parser.add_argument("--dry", action="store_true", help="Print to stdout, don't write.")
    parser.add_argument("--raw", action="store_true", help="Dump raw HTML and exit.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    html = fetch_html(args.url)
    if args.raw:
        sys.stdout.write(html)
        return

    parser_ = _HeroBlockParser()
    parser_.feed(html)
    heroes = _extract_heroes(parser_.cards)
    log.info("Extracted %d hero card(s) from %s", len(heroes), args.url)

    if not heroes:
        raise SystemExit(
            "No heroes parsed. The page structure has likely changed — "
            "rerun with --raw and adjust _extract_heroes / _HeroBlockParser."
        )

    payload = {
        "_meta": {
            "version": 1,
            "source": args.url,
            "note": "Generated by wagame.tools.scrape_kohqs. Edit by hand if needed.",
        },
        "heroes": heroes,
    }
    output = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if args.dry:
        sys.stdout.write(output)
    else:
        Path(args.out).write_text(output, encoding="utf-8")
        log.info("Wrote %d heroes to %s", len(heroes), args.out)


if __name__ == "__main__":
    main()
