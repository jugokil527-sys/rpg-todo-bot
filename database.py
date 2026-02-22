"""
database.py — Async SQLite database for RPG Todo Bot.
Tables: users, tasks, rewards, whitelist, idea_categories, ideas.
"""

import aiosqlite
import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

DB_PATH = "bot.db"


class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self.db: aiosqlite.Connection | None = None

    async def init(self):
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self._create_tables()
        logger.info("Database initialized")

    async def close(self):
        if self.db:
            await self.db.close()

    async def _create_tables(self):
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT DEFAULT '',
                level      INTEGER DEFAULT 1,
                xp         INTEGER DEFAULT 0,
                hp         INTEGER DEFAULT 100,
                points     INTEGER DEFAULT 0,
                shield_active   INTEGER DEFAULT 0,
                pepper_mode     INTEGER DEFAULT 0,
                pepper_streak   INTEGER DEFAULT 0,
                last_perfect_date TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                title         TEXT NOT NULL,
                task_type     TEXT NOT NULL,
                reminder_time TEXT DEFAULT '',
                completed     INTEGER DEFAULT 0,
                created_date  TEXT NOT NULL,
                completed_at  TEXT DEFAULT '',
                penalized     INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS rewards (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                title      TEXT NOT NULL,
                cost       INTEGER NOT NULL,
                claimed    INTEGER DEFAULT 0,
                claimed_at TEXT DEFAULT '',
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS whitelist (
                user_id  INTEGER PRIMARY KEY,
                added_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS idea_categories (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id  INTEGER NOT NULL,
                name     TEXT NOT NULL,
                emoji    TEXT DEFAULT '📂',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS ideas (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                title       TEXT NOT NULL,
                status      TEXT DEFAULT 'new',
                created_at  TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (category_id) REFERENCES idea_categories(id)
            );
        """)
        await self.db.commit()

    # ── Users ──────────────────────────────────────────────

    async def get_user(self, user_id: int) -> dict | None:
        async with self.db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def create_user(self, user_id: int, username: str = ""):
        await self.db.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username),
        )
        await self.db.commit()

    async def update_user(self, user_id: int, **kwargs):
        if not kwargs:
            return
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [user_id]
        await self.db.execute(
            f"UPDATE users SET {sets} WHERE user_id = ?", vals
        )
        await self.db.commit()

    async def get_all_user_ids(self) -> list[int]:
        async with self.db.execute("SELECT user_id FROM whitelist") as cur:
            rows = await cur.fetchall()
            return [r["user_id"] for r in rows]

    # ── Tasks ──────────────────────────────────────────────

    async def add_task(
        self,
        user_id: int,
        title: str,
        task_type: str,
        reminder_time: str | None,
        created_date: str,
    ) -> int:
        async with self.db.execute(
            """INSERT INTO tasks (user_id, title, task_type, reminder_time, created_date)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, title, task_type, reminder_time or "", created_date),
        ) as cur:
            task_id = cur.lastrowid
        await self.db.commit()
        return task_id

    async def get_task(self, task_id: int) -> dict | None:
        async with self.db.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_tasks_by_date(self, user_id: int, iso_date: str) -> list[dict]:
        async with self.db.execute(
            "SELECT * FROM tasks WHERE user_id = ? AND created_date = ? ORDER BY id",
            (user_id, iso_date),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def complete_task(self, task_id: int):
        await self.db.execute(
            "UPDATE tasks SET completed = 1, completed_at = ? WHERE id = ?",
            (datetime.now().isoformat(), task_id),
        )
        await self.db.commit()

    async def delete_task(self, task_id: int):
        await self.db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await self.db.commit()

    async def mark_tasks_penalized(self, user_id: int, iso_date: str):
        await self.db.execute(
            """UPDATE tasks SET penalized = 1
               WHERE user_id = ? AND created_date = ? AND completed = 0""",
            (user_id, iso_date),
        )
        await self.db.commit()

    # ── Rewards ────────────────────────────────────────────

    async def add_reward(self, user_id: int, title: str, cost: int) -> int:
        async with self.db.execute(
            "INSERT INTO rewards (user_id, title, cost) VALUES (?, ?, ?)",
            (user_id, title, cost),
        ) as cur:
            rid = cur.lastrowid
        await self.db.commit()
        return rid

    async def get_rewards(self, user_id: int) -> list[dict]:
        async with self.db.execute(
            "SELECT * FROM rewards WHERE user_id = ? AND claimed = 0 ORDER BY id",
            (user_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_reward(self, reward_id: int) -> dict | None:
        async with self.db.execute(
            "SELECT * FROM rewards WHERE id = ?", (reward_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def claim_reward(self, reward_id: int):
        await self.db.execute(
            "UPDATE rewards SET claimed = 1, claimed_at = ? WHERE id = ?",
            (datetime.now().isoformat(), reward_id),
        )
        await self.db.commit()

    async def delete_reward(self, reward_id: int):
        await self.db.execute("DELETE FROM rewards WHERE id = ?", (reward_id,))
        await self.db.commit()

    # ── Whitelist ──────────────────────────────────────────

    async def add_to_whitelist(self, user_id: int):
        await self.db.execute(
            "INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (user_id,)
        )
        await self.db.commit()

    async def remove_from_whitelist(self, user_id: int):
        await self.db.execute(
            "DELETE FROM whitelist WHERE user_id = ?", (user_id,)
        )
        await self.db.commit()

    async def is_whitelisted(self, user_id: int) -> bool:
        async with self.db.execute(
            "SELECT 1 FROM whitelist WHERE user_id = ?", (user_id,)
        ) as cur:
            return await cur.fetchone() is not None

    async def get_whitelist(self) -> list[dict]:
        async with self.db.execute("SELECT * FROM whitelist ORDER BY added_at") as cur:
            return [dict(r) for r in await cur.fetchall()]

    # ── Analytics ──────────────────────────────────────────

    async def get_week_completion_rate(self, user_id: int) -> float:
        """Return completion % for the last 7 days."""
        today = date.today()
        week_ago = (today - timedelta(days=7)).isoformat()
        today_s = today.isoformat()

        async with self.db.execute(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN completed = 1 THEN 1 ELSE 0 END) as done
               FROM tasks
               WHERE user_id = ? AND created_date >= ? AND created_date <= ?""",
            (user_id, week_ago, today_s),
        ) as cur:
            row = await cur.fetchone()
            total = row["total"]
            done = row["done"] or 0
            return (done / total * 100) if total > 0 else 0.0

    # ── Idea Categories ────────────────────────────────────

    async def add_category(self, user_id: int, name: str, emoji: str = "📂") -> int:
        async with self.db.execute(
            "INSERT INTO idea_categories (user_id, name, emoji) VALUES (?, ?, ?)",
            (user_id, name, emoji),
        ) as cur:
            cid = cur.lastrowid
        await self.db.commit()
        return cid

    async def get_categories(self, user_id: int) -> list[dict]:
        async with self.db.execute(
            "SELECT * FROM idea_categories WHERE user_id = ? ORDER BY id",
            (user_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_category(self, cat_id: int) -> dict | None:
        async with self.db.execute(
            "SELECT * FROM idea_categories WHERE id = ?", (cat_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def delete_category(self, cat_id: int):
        await self.db.execute("DELETE FROM ideas WHERE category_id = ?", (cat_id,))
        await self.db.execute("DELETE FROM idea_categories WHERE id = ?", (cat_id,))
        await self.db.commit()

    async def count_ideas_in_category(self, cat_id: int) -> int:
        async with self.db.execute(
            "SELECT COUNT(*) as cnt FROM ideas WHERE category_id = ?", (cat_id,)
        ) as cur:
            row = await cur.fetchone()
            return row["cnt"]

    # ── Ideas ──────────────────────────────────────────────

    async def add_idea(self, user_id: int, category_id: int, title: str) -> int:
        async with self.db.execute(
            "INSERT INTO ideas (user_id, category_id, title) VALUES (?, ?, ?)",
            (user_id, category_id, title),
        ) as cur:
            iid = cur.lastrowid
        await self.db.commit()
        return iid

    async def get_ideas_by_category(self, category_id: int) -> list[dict]:
        async with self.db.execute(
            "SELECT * FROM ideas WHERE category_id = ? ORDER BY id",
            (category_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_idea(self, idea_id: int) -> dict | None:
        async with self.db.execute(
            "SELECT * FROM ideas WHERE id = ?", (idea_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_idea_status(self, idea_id: int, status: str):
        await self.db.execute(
            "UPDATE ideas SET status = ? WHERE id = ?", (status, idea_id)
        )
        await self.db.commit()

    async def delete_idea(self, idea_id: int):
        await self.db.execute("DELETE FROM ideas WHERE id = ?", (idea_id,))
        await self.db.commit()
