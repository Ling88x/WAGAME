"""/heroes and /hero — owned-roster grid and catalog lookup.

Players acquire heroes by spending gems in `/summon`, or via
`/admin grant-hero`. Display is ephemeral. The owned-roster view stacks
up to 9 mini-embeds (one per hero, rarity-colored, with optional
portrait thumbnail) plus a header embed; pagination buttons handle the
overflow. Both `/hero` and `/admin grant-hero` share a name+codename
autocomplete so the user doesn't have to memorize slugs like `ghostpink`.
"""

from __future__ import annotations

import json

import discord
from discord import app_commands
from discord.ext import commands

from wagame.db import Database
from wagame.ui import NEUTRAL_COLOR, Outcome, toast

RARITY_COLOR: dict[str, discord.Color] = {
    "common":    discord.Color.light_grey(),
    "uncommon":  discord.Color.green(),
    "rare":      discord.Color.blue(),
    "epic":      discord.Color.purple(),
    "legendary": discord.Color.gold(),
    "mythic":    discord.Color.red(),
}

RARITY_EMOJI: dict[str, str] = {
    "common":    "⚪",
    "uncommon":  "🟢",
    "rare":      "🔵",
    "epic":      "🟣",
    "legendary": "🟡",
    "mythic":    "🔴",
}

RARITY_ORDER = ("mythic", "legendary", "epic", "rare", "uncommon", "common")

# Discord caps a message at 10 embeds. We use 1 for the header and 9 for heroes.
HEROES_PER_PAGE = 9


# -- shared helpers ---------------------------------------------------------


async def _autocomplete_hero(
    db: Database, query: str, limit: int = 25
) -> list[app_commands.Choice[str]]:
    """Suggest heroes by display name OR codename (case-insensitive contains).

    Both `name` (what the dropdown shows) and `value` (what gets submitted)
    use the hero's display name — codenames are an implementation detail
    nobody should have to memorize. Display names on the kohqs roster are
    unique, so we look the hero up by name on the receiving end.
    """
    q = f"%{query.strip().lower()}%"
    async with db.conn.execute(
        """
        SELECT codename, name, rarity
        FROM heroes
        WHERE LOWER(name) LIKE ? OR LOWER(codename) LIKE ?
        ORDER BY
            CASE rarity
                WHEN 'mythic'    THEN 0
                WHEN 'legendary' THEN 1
                WHEN 'epic'      THEN 2
                WHEN 'rare'      THEN 3
                WHEN 'uncommon'  THEN 4
                WHEN 'common'    THEN 5
                ELSE 6
            END,
            name
        LIMIT ?
        """,
        (q, q, limit),
    ) as cur:
        rows = await cur.fetchall()
    return [
        app_commands.Choice(
            name=f"{RARITY_EMOJI.get(r['rarity'], '•')} {r['name']}",
            value=r["name"],
        )
        for r in rows
    ]


# -- rendering --------------------------------------------------------------


def _hero_card_embed(row, *, level: int | None = None, dupes: int = 0) -> discord.Embed:
    """One hero -> one mini-embed, colored by rarity, thumbnail if available."""
    color = RARITY_COLOR.get(row["rarity"], NEUTRAL_COLOR)
    title = row["name"]
    if level is not None:
        title = f"{title} · Lv {level}"
        if dupes:
            title += f" (+{dupes})"

    embed = discord.Embed(title=title, color=color)
    if row["image_url"]:
        embed.set_thumbnail(url=row["image_url"])

    rarity_label = row["rarity"].capitalize()
    traits = [f"{RARITY_EMOJI.get(row['rarity'], '•')} {rarity_label}"]
    if row["terrain"]:
        traits.append(row["terrain"].capitalize())
    if row["house"]:
        traits.append(row["house"])
    embed.description = " · ".join(traits)
    embed.set_footer(text=f"codename: {row['codename']}")
    return embed


def _summary_embed(user: discord.abc.User, rows) -> discord.Embed:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["rarity"]] = counts.get(r["rarity"], 0) + 1
    tally = " · ".join(
        f"{RARITY_EMOJI.get(rar, '•')} {counts[rar]}"
        for rar in RARITY_ORDER
        if rar in counts
    )

    embed = discord.Embed(
        title=f"🎴 {user.display_name}'s Heroes",
        color=NEUTRAL_COLOR,
        description=f"You own **{len(rows)}** hero(es).\n{tally}" if tally else None,
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    return embed


def _build_page_embeds(user: discord.abc.User, rows, page: int) -> list[discord.Embed]:
    total_pages = max(1, (len(rows) + HEROES_PER_PAGE - 1) // HEROES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    header = _summary_embed(user, rows)
    if total_pages > 1:
        header.set_footer(text=f"Page {page + 1}/{total_pages}")

    start = page * HEROES_PER_PAGE
    slice_ = rows[start : start + HEROES_PER_PAGE]
    cards = [
        _hero_card_embed(r, level=r["level"], dupes=r["dupes_pending"]) for r in slice_
    ]
    return [header, *cards]


# -- pagination view --------------------------------------------------------


class HeroesView(discord.ui.View):
    def __init__(self, db: Database, owner_id: int, total: int) -> None:
        super().__init__(timeout=15 * 60)
        self.db = db
        self.owner_id = owner_id
        self.page = 0
        self.total_pages = max(1, (total + HEROES_PER_PAGE - 1) // HEROES_PER_PAGE)
        # Hide the pager entirely when there's only one page.
        if self.total_pages == 1:
            self.clear_items()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=toast("This isn't your panel.", Outcome.ERROR),
                ephemeral=True,
            )
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction) -> None:
        rows = await _fetch_owned(self.db, interaction.user.id)
        # Owned count could shift (admin granted between clicks); recompute pages.
        self.total_pages = max(1, (len(rows) + HEROES_PER_PAGE - 1) // HEROES_PER_PAGE)
        self.page = max(0, min(self.page, self.total_pages - 1))
        embeds = _build_page_embeds(interaction.user, rows, self.page)
        await interaction.response.edit_message(embeds=embeds, view=self)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page = (self.page - 1) % self.total_pages
        await self._refresh(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page = (self.page + 1) % self.total_pages
        await self._refresh(interaction)


# -- queries ----------------------------------------------------------------


async def _fetch_owned(db: Database, user_id: int):
    async with db.conn.execute(
        """
        SELECT h.codename, h.name, h.rarity, h.house, h.terrain, h.image_url,
               o.level, o.dupes_pending
        FROM owned_heroes o
        JOIN heroes h ON h.id = o.hero_id
        WHERE o.discord_user_id = ?
        ORDER BY
            CASE h.rarity
                WHEN 'mythic'    THEN 0
                WHEN 'legendary' THEN 1
                WHEN 'epic'      THEN 2
                WHEN 'rare'      THEN 3
                WHEN 'uncommon'  THEN 4
                WHEN 'common'    THEN 5
                ELSE 6
            END,
            h.name
        """,
        (user_id,),
    ) as cur:
        return await cur.fetchall()


# -- cog --------------------------------------------------------------------


class HeroesCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(name="heroes", description="Show the heroes you own.")
    async def heroes(self, interaction: discord.Interaction) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        rows = await _fetch_owned(self.db, interaction.user.id)

        if not rows:
            await interaction.response.send_message(
                embed=toast(
                    "You don't own any heroes yet. Try `/summon hero:<name>` to "
                    "spend gems on a hero, or ask an admin for `/admin grant-hero`.",
                    Outcome.INFO,
                ),
                ephemeral=True,
            )
            return

        embeds = _build_page_embeds(interaction.user, rows, page=0)
        view = HeroesView(self.db, interaction.user.id, total=len(rows))
        await interaction.response.send_message(
            embeds=embeds, view=view if view.children else None, ephemeral=True
        )

    @app_commands.command(
        name="hero", description="Show full info about a hero from the catalog."
    )
    @app_commands.describe(hero="Search by name or codename — pick from suggestions.")
    async def hero(self, interaction: discord.Interaction, hero: str) -> None:
        # Autocomplete submits the display name; we still accept a raw codename
        # as a power-user fallback so legacy links / muscle-memory don't break.
        needle = hero.strip().lower()
        async with self.db.conn.execute(
            "SELECT * FROM heroes WHERE LOWER(name) = ?", (needle,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            async with self.db.conn.execute(
                "SELECT * FROM heroes WHERE codename = ?", (needle,)
            ) as cur:
                row = await cur.fetchone()

        if row is None:
            await interaction.response.send_message(
                embed=toast(
                    f"No hero matches `{hero}`. Try the autocomplete suggestions.",
                    Outcome.ERROR,
                ),
                ephemeral=True,
            )
            return

        embed = _hero_detail_embed(row)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @hero.autocomplete("hero")
    async def hero_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await _autocomplete_hero(self.db, current)


def _hero_detail_embed(row) -> discord.Embed:
    bonuses = json.loads(row["bonuses_json"])
    tags = json.loads(row["tags_json"])
    color = RARITY_COLOR.get(row["rarity"], NEUTRAL_COLOR)
    rarity_label = row["rarity"].capitalize()

    embed = discord.Embed(
        title=f"{RARITY_EMOJI.get(row['rarity'], '•')} {row['name']}",
        color=color,
    )
    if row["image_url"]:
        embed.set_thumbnail(url=row["image_url"])
    embed.add_field(name="Codename", value=f"`{row['codename']}`", inline=True)
    embed.add_field(name="Rarity", value=rarity_label, inline=True)
    if row["release_date"]:
        embed.add_field(name="Released", value=row["release_date"], inline=True)
    if row["element"]:
        embed.add_field(name="Element", value=row["element"], inline=True)
    if row["house"]:
        embed.add_field(name="House", value=row["house"], inline=True)
    if row["terrain"]:
        embed.add_field(name="Terrain", value=row["terrain"].capitalize(), inline=True)
    if bonuses:
        embed.add_field(
            name="Bonuses",
            value="\n".join(f"• {b}" for b in bonuses),
            inline=False,
        )
    if tags:
        embed.set_footer(text=" · ".join(tags))
    return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HeroesCog(bot))
