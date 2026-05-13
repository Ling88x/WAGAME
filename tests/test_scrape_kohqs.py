"""Light tests for the kohqs scraper helpers — just the bits we changed."""

from __future__ import annotations

from wagame.tools.scrape_kohqs import _parse_hero_block, extract_heroes

# Minimal block carrying just what the regex hooks need. Real pages have
# hundreds of lines per hero; this is enough to exercise the parser.
_FAKE_BLOCK = (
    '<div class="row rowwa rowdetail">'
    '<a href="/wa/heroes/ghostpink">'
    '<img src="hero_rarity_rare.png">'
    '<img src="biome_forest_3.png">'
    '<b>Roselda Graves</b>'
    '<span class="smaller">cool</span>'
    '<div class="abilities">'
    'Bonus One<br>Bonus Two'
    '<div class="rowlink"><a href="#">Details</a></div>'
    '</div></a></div>'
)


def test_parse_hero_block_skips_image_when_portrait_base_missing() -> None:
    hero = _parse_hero_block(_FAKE_BLOCK)
    assert hero is not None
    assert hero["codename"] == "ghostpink"
    assert hero["image_url"] is None


def test_parse_hero_block_builds_image_url_when_portrait_base_set() -> None:
    hero = _parse_hero_block(_FAKE_BLOCK, portrait_base="https://kohqs.com/img/wa")
    assert hero is not None
    assert hero["image_url"] == "https://kohqs.com/img/wa/ghostpink.png"


def test_parse_hero_block_strips_trailing_slash_from_portrait_base() -> None:
    hero = _parse_hero_block(_FAKE_BLOCK, portrait_base="https://kohqs.com/img/")
    assert hero is not None
    assert hero["image_url"] == "https://kohqs.com/img/ghostpink.png"


def test_extract_heroes_passes_portrait_base_through() -> None:
    heroes = extract_heroes(_FAKE_BLOCK, portrait_base="https://x.test")
    assert len(heroes) == 1
    assert heroes[0]["image_url"] == "https://x.test/ghostpink.png"
