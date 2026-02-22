"""
utils.py — Helper functions: MarkdownV2 escaping, HP/XP bars, image paths.
"""

import os
import re

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# Characters that MUST be escaped in Telegram MarkdownV2
_MD2_SPECIAL = re.compile(r'([_\*\[\]\(\)~`>#+\-=|{}\.\!\\])')


def escape_md(text: str) -> str:
    """Escape all MarkdownV2 special characters."""
    if text is None:
        return ""
    return _MD2_SPECIAL.sub(r'\\\1', str(text))


def render_bar(current: int, maximum: int, length: int = 10) -> str:
    """Render a progress bar: [███░░░░░░░]"""
    ratio = max(0.0, min(1.0, current / maximum)) if maximum > 0 else 0.0
    filled = round(ratio * length)
    empty = length - filled
    return f"\\[{'█' * filled}{'░' * empty}\\]"


def render_hp_bar(hp: int) -> str:
    return f"❤️ {render_bar(hp, 100)}"


def render_xp_bar(xp: int, xp_needed: int) -> str:
    return f"⚔️ {render_bar(xp, xp_needed)}"


def get_profile_image_path(hp: int, pepper_mode: bool) -> str:
    """Pick profile image based on HP and pepper mode."""
    if hp < 30:
        name = "profile_low_hp.jpg"
    elif pepper_mode:
        name = "profile_pepper.jpg"
    else:
        name = "profile_normal.jpg"
    return os.path.join(ASSETS_DIR, name)


def get_penalty_image_path() -> str:
    return os.path.join(ASSETS_DIR, "penalty_alert.jpg")


# ── Time parsing (regex: 16:00, 16.00, 16 00) ─────────

_TIME_RE = re.compile(r'^(\d{1,2})[\s:\.](\d{2})$')


def parse_time(text: str) -> tuple[int, int] | None:
    """
    Parse user time input. Accepts:
      16:00, 16.00, 16 00, 9:05, 9.05
    Returns (hour, minute) or None on failure.
    """
    m = _TIME_RE.match(text.strip())
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return (hour, minute)
    return None
