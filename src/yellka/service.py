from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .catalog import VECTORS, find_catalog_item, require_vector
from .money import db_ap, parse_ap

DEFAULT_BASE_RATE = Decimal("0.200")
CORE_STEP = Decimal("0.050")
FULL_CLOSE_MULTIPLIER = Decimal("1.500")
PRIORITY_MULTIPLIER = Decimal("2.000")
VECTOR_STEP = Decimal("0.100")
VECTOR_MAX_LEVEL = 10
CASHBACK_MAX_LEVEL = 5
RETROACTIVE_INDEXING_COST = Decimal("25.000")
UPDATE_BONUS = Decimal("2.500")


class EconomyError(RuntimeError):
    pass


class InsufficientBalanceError(EconomyError):
    pass


@dataclass(frozen=True)
class EconomyState:
    balance: Decimal
    base_rate: Decimal
    cashback_level: int
    retroactive_indexing_enabled: bool
    vector_levels: dict[str, int]


@dataclass(frozen=True)
class TaskResult:
    id: int
    reward: Decimal
    retro_bonus: Decimal


@dataclass(frozen=True)
class UpgradeResult:
    id: int
    upgrade_type: str
    target: str
    cost: Decimal
    cashback: Decimal


class EconomyService:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._ensure_schema(conn)
            self._ensure_defaults(conn)

    def initialize(
        self,
        *,
        initial_balance: Decimal | int | str = Decimal("0"),
        update_bonus: bool = False,
    ) -> EconomyState:
        amount = parse_ap(initial_balance)
        if amount:
            self.add_income(amount, "Начальный баланс")
        if update_bonus:
            self.add_income(UPDATE_BONUS, "Бонус АССИРа за обновление")
        return self.get_state()

    def get_state(self) -> EconomyState:
        with self._connect() as conn:
            return EconomyState(
                balance=self._balance(conn),
                base_rate=self._get_decimal(conn, "base_rate"),
                cashback_level=self._get_int(conn, "cashback_level"),
                retroactive_indexing_enabled=self._get_bool(
                    conn, "retroactive_indexing_enabled"
                ),
                vector_levels={
                    key: self._get_int(conn, f"vector_level:{key}") for key in VECTORS
                },
            )

    def add_income(
        self, amount: Decimal | int | str, note: str, *, kind: str = "income"
    ) -> int:
        return self._add_transaction(parse_ap(amount), kind, note)

    def add_expense(
        self,
        amount: Decimal | int | str,
        note: str,
        *,
        allow_negative: bool = False,
        kind: str = "expense",
    ) -> int:
        return self._add_transaction(
            -parse_ap(amount), kind, note, allow_negative=allow_negative
        )

    def complete_task(
        self,
        *,
        title: str | None = None,
        vector: str = "code",
        units: int = 1,
        catalog_key: str | None = None,
        catalog_value: Decimal | int | str | None = None,
        priority: bool = False,
        full_close: bool = False,
        note: str = "",
    ) -> TaskResult:
        if units < 1:
            raise ValueError("units must be at least 1")
        vector_info = require_vector(vector)
        item_title = title
        if catalog_key:
            item = find_catalog_item(catalog_key)
            item_title = item_title or item.title
            task_weight = item.value
        else:
            task_weight = parse_ap(catalog_value or Decimal("1"))
            item_title = item_title or "Задача"

        with self._connect() as conn:
            base_rate = self._get_decimal(conn, "base_rate")
            vector_level = self._get_int(conn, f"vector_level:{vector_info.key}")
            vector_multiplier = parse_ap(Decimal("1") + VECTOR_STEP * vector_level)
            priority_multiplier = PRIORITY_MULTIPLIER if priority else Decimal("1.000")
            full_close_bonus = FULL_CLOSE_MULTIPLIER if full_close else Decimal("1.000")
            reward = parse_ap(
                Decimal(units)
                * base_rate
                * task_weight
                * vector_multiplier
                * priority_multiplier
                * full_close_bonus
            )

            now = self._now()
            cur = conn.execute(
                """
                INSERT INTO tasks (
                    created_at, title, vector, units, base_rate, vector_level,
                    vector_multiplier, priority_multiplier, full_close_bonus,
                    catalog_weight, reward, retro_paid_base_rate, note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    item_title,
                    vector_info.key,
                    units,
                    db_ap(base_rate),
                    vector_level,
                    db_ap(vector_multiplier),
                    db_ap(priority_multiplier),
                    db_ap(full_close_bonus),
                    db_ap(task_weight),
                    db_ap(reward),
                    db_ap(base_rate),
                    note,
                ),
            )
            task_id = int(cur.lastrowid)
            self._insert_transaction(
                conn,
                reward,
                "task_reward",
                f"Выполнена задача: {item_title}",
                task_id=task_id,
            )
            retro_bonus = self._apply_retroactive_indexing(conn, exclude_task_id=task_id)
            return TaskResult(task_id, reward, retro_bonus)

    def buy_core(self) -> UpgradeResult:
        with self._connect() as conn:
            current_base = self._get_decimal(conn, "base_rate")
            new_base = parse_ap(current_base + CORE_STEP)
            cost = parse_ap(current_base * Decimal("10"))
            result = self._buy_upgrade(
                conn,
                upgrade_type="core",
                target="Ядро Вычислений",
                level_before=db_ap(current_base),
                level_after=db_ap(new_base),
                cost=cost,
                cashback_eligible=True,
            )
            self._set_meta(conn, "base_rate", db_ap(new_base))
            return result

    def buy_vector(self, vector: str) -> UpgradeResult:
        vector_info = require_vector(vector)
        with self._connect() as conn:
            current_level = self._get_int(conn, f"vector_level:{vector_info.key}")
            if current_level >= VECTOR_MAX_LEVEL:
                raise EconomyError(f"Vector {vector_info.title} is already maxed")
            new_level = current_level + 1
            cost = parse_ap(Decimal(new_level) * Decimal("0.5"))
            result = self._buy_upgrade(
                conn,
                upgrade_type="vector",
                target=vector_info.key,
                level_before=str(current_level),
                level_after=str(new_level),
                cost=cost,
                cashback_eligible=True,
            )
            self._set_meta(conn, f"vector_level:{vector_info.key}", str(new_level))
            return result

    def buy_cashback(self) -> UpgradeResult:
        with self._connect() as conn:
            current_level = self._get_int(conn, "cashback_level")
            if current_level >= CASHBACK_MAX_LEVEL:
                raise EconomyError("Cashback is already maxed")
            new_level = current_level + 1
            result = self._buy_upgrade(
                conn,
                upgrade_type="cashback",
                target="Кэшбек-Шина Терминала",
                level_before=str(current_level),
                level_after=str(new_level),
                cost=Decimal("3.000"),
                cashback_eligible=False,
            )
            self._set_meta(conn, "cashback_level", str(new_level))
            return result

    def buy_retroactive_indexing(self) -> UpgradeResult:
        with self._connect() as conn:
            if self._get_bool(conn, "retroactive_indexing_enabled"):
                raise EconomyError("Retroactive indexing is already enabled")
            result = self._buy_upgrade(
                conn,
                upgrade_type="retroactive_indexing",
                target="Протокол: Ретроспективная Индексация",
                level_before="0",
                level_after="1",
                cost=RETROACTIVE_INDEXING_COST,
                cashback_eligible=False,
            )
            self._set_meta(conn, "retroactive_indexing_enabled", "1")
            return result

    def list_transactions(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, amount, kind, note, task_id, upgrade_id
                FROM transactions
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_tasks(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, title, vector, units, base_rate,
                    vector_level, vector_multiplier, priority_multiplier,
                    full_close_bonus, catalog_weight, reward, retro_paid_base_rate, note
                FROM tasks
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_upgrades(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, upgrade_type, target, level_before,
                    level_after, cost, cashback, note
                FROM upgrades
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _buy_upgrade(
        self,
        conn: sqlite3.Connection,
        *,
        upgrade_type: str,
        target: str,
        level_before: str,
        level_after: str,
        cost: Decimal,
        cashback_eligible: bool,
    ) -> UpgradeResult:
        cost = parse_ap(cost)
        cashback_level = self._get_int(conn, "cashback_level")
        cashback = (
            parse_ap(cost * Decimal(cashback_level) * Decimal("0.05"))
            if cashback_eligible and cashback_level
            else Decimal("0.000")
        )
        note = f"{target}: {level_before} -> {level_after}"
        self._ensure_can_spend(conn, cost)
        now = self._now()
        cur = conn.execute(
            """
            INSERT INTO upgrades (
                created_at, upgrade_type, target, level_before, level_after,
                cost, cashback, note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                upgrade_type,
                target,
                level_before,
                level_after,
                db_ap(cost),
                db_ap(cashback),
                note,
            ),
        )
        upgrade_id = int(cur.lastrowid)
        self._insert_transaction(
            conn,
            -cost,
            "upgrade_purchase",
            f"Покупка улучшения: {note}",
            upgrade_id=upgrade_id,
        )
        if cashback:
            self._insert_transaction(
                conn,
                cashback,
                "cashback",
                f"Кэшбек за улучшение: {note}",
                upgrade_id=upgrade_id,
            )
        return UpgradeResult(upgrade_id, upgrade_type, target, cost, cashback)

    def _apply_retroactive_indexing(
        self, conn: sqlite3.Connection, *, exclude_task_id: int
    ) -> Decimal:
        if not self._get_bool(conn, "retroactive_indexing_enabled"):
            return Decimal("0.000")
        current_base = self._get_decimal(conn, "base_rate")
        rows = conn.execute(
            """
            SELECT id, units, vector_multiplier, priority_multiplier,
                full_close_bonus, catalog_weight, retro_paid_base_rate
            FROM tasks
            WHERE id != ?
            ORDER BY id ASC
            """,
            (exclude_task_id,),
        ).fetchall()
        total = Decimal("0.000")
        changed = 0
        for row in rows:
            paid_base = parse_ap(row["retro_paid_base_rate"])
            if paid_base >= current_base:
                continue
            delta = parse_ap(
                Decimal(row["units"])
                * (current_base - paid_base)
                * parse_ap(row["vector_multiplier"])
                * parse_ap(row["priority_multiplier"])
                * parse_ap(row["full_close_bonus"])
                * parse_ap(row["catalog_weight"])
            )
            if not delta:
                continue
            total = parse_ap(total + delta)
            changed += 1
            conn.execute(
                "UPDATE tasks SET retro_paid_base_rate = ? WHERE id = ?",
                (db_ap(current_base), row["id"]),
            )
        if total:
            self._insert_transaction(
                conn,
                total,
                "retro_bonus",
                f"Ретроспективная индексация по задачам: {changed}",
            )
        return total

    def _add_transaction(
        self,
        amount: Decimal,
        kind: str,
        note: str,
        *,
        allow_negative: bool = False,
    ) -> int:
        with self._connect() as conn:
            if amount < 0 and not allow_negative:
                self._ensure_can_spend(conn, -amount)
            return self._insert_transaction(conn, amount, kind, note)

    def _insert_transaction(
        self,
        conn: sqlite3.Connection,
        amount: Decimal,
        kind: str,
        note: str,
        *,
        task_id: int | None = None,
        upgrade_id: int | None = None,
    ) -> int:
        cur = conn.execute(
            """
            INSERT INTO transactions (created_at, amount, kind, note, task_id, upgrade_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (self._now(), db_ap(amount), kind, note, task_id, upgrade_id),
        )
        return int(cur.lastrowid)

    def _ensure_can_spend(self, conn: sqlite3.Connection, amount: Decimal) -> None:
        balance = self._balance(conn)
        if parse_ap(balance - amount) < 0:
            raise InsufficientBalanceError(
                f"Not enough AP: need {db_ap(amount)}, balance {db_ap(balance)}"
            )

    def _balance(self, conn: sqlite3.Connection) -> Decimal:
        rows = conn.execute("SELECT amount FROM transactions").fetchall()
        total = sum((parse_ap(row["amount"]) for row in rows), Decimal("0.000"))
        return parse_ap(total)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                amount TEXT NOT NULL,
                kind TEXT NOT NULL,
                note TEXT NOT NULL,
                task_id INTEGER,
                upgrade_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                title TEXT NOT NULL,
                vector TEXT NOT NULL,
                units INTEGER NOT NULL,
                base_rate TEXT NOT NULL,
                vector_level INTEGER NOT NULL,
                vector_multiplier TEXT NOT NULL,
                priority_multiplier TEXT NOT NULL,
                full_close_bonus TEXT NOT NULL,
                catalog_weight TEXT NOT NULL,
                reward TEXT NOT NULL,
                retro_paid_base_rate TEXT NOT NULL,
                note TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS upgrades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                upgrade_type TEXT NOT NULL,
                target TEXT NOT NULL,
                level_before TEXT NOT NULL,
                level_after TEXT NOT NULL,
                cost TEXT NOT NULL,
                cashback TEXT NOT NULL,
                note TEXT NOT NULL
            );
            """
        )

    def _ensure_defaults(self, conn: sqlite3.Connection) -> None:
        defaults = {
            "schema_version": "1",
            "base_rate": db_ap(DEFAULT_BASE_RATE),
            "cashback_level": "0",
            "retroactive_indexing_enabled": "0",
        }
        defaults.update({f"vector_level:{key}": "0" for key in VECTORS})
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO metadata (key, value) VALUES (?, ?)",
                (key, value),
            )

    def _set_meta(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            """
            INSERT INTO metadata (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def _get_meta(self, conn: sqlite3.Connection, key: str) -> str:
        row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise EconomyError(f"Missing metadata key: {key}")
        return str(row["value"])

    def _get_decimal(self, conn: sqlite3.Connection, key: str) -> Decimal:
        return parse_ap(self._get_meta(conn, key))

    def _get_int(self, conn: sqlite3.Connection, key: str) -> int:
        return int(self._get_meta(conn, key))

    def _get_bool(self, conn: sqlite3.Connection, key: str) -> bool:
        return self._get_meta(conn, key) == "1"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
