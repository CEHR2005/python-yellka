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
CASHBACK_PURCHASE_COST = Decimal("3.000")
RETROACTIVE_INDEXING_COST = Decimal("25.000")
UPDATE_BONUS = Decimal("2.500")
DEFAULT_DISCOUNT_START_BASE = Decimal("0.800")
DEFAULT_DISCOUNT_PURCHASE_COST = Decimal("15.000")
DEFAULT_HISTORICAL_DISCOUNT_CASHBACK = Decimal("18.270")
DEFAULT_HISTORICAL_RETROACTIVE_INDEXING_COST = Decimal("20.000")
DEFAULT_HISTORICAL_STARTING_BALANCE = Decimal("0.000")
DEFAULT_HISTORICAL_RETRO_BONUS = Decimal("0.000")


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
    retro_details: list[RetroBonusDetail]


@dataclass(frozen=True)
class RetroBonusDetail:
    task_id: int
    title: str
    units: int
    paid_base_rate: Decimal
    current_base_rate: Decimal
    vector_multiplier: Decimal
    priority_multiplier: Decimal
    full_close_bonus: Decimal
    catalog_weight: Decimal
    previous_reward: Decimal
    updated_reward: Decimal
    delta: Decimal


@dataclass(frozen=True)
class UpgradeResult:
    id: int
    upgrade_type: str
    target: str
    cost: Decimal
    cashback: Decimal


@dataclass(frozen=True)
class UpgradeQuote:
    target: str
    level_before: int | Decimal
    level_after: int | Decimal
    full_cost: Decimal
    discount: Decimal
    final_cost: Decimal
    maxed: bool = False


@dataclass(frozen=True)
class UpgradeSpendEstimate:
    core_spent: Decimal
    vector_spent: Decimal
    discount_spent: Decimal
    discount_saved: Decimal
    retroactive_indexing_spent: Decimal
    vector_spent_by_key: dict[str, Decimal]
    total_spent: Decimal
    discount_start_base: Decimal


@dataclass(frozen=True)
class EarningsStats:
    total_earned: Decimal
    starting_balance: Decimal
    task_earned: Decimal
    retro_earned: Decimal
    discount_gross: Decimal
    discount_net: Decimal
    premium_earned: Decimal
    other_earned: Decimal
    premium_and_other_earned: Decimal


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
        amount = abs(parse_ap(amount))
        return self._add_transaction(
            -amount, kind, note, allow_negative=allow_negative
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
        category, item_title = self._split_task_title(item_title)

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
                    created_at, category, title, vector, units, base_rate, vector_level,
                    vector_multiplier, priority_multiplier, full_close_bonus,
                    catalog_weight, reward, current_reward,
                    retro_paid_base_rate, premium_received, note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    category,
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
                    db_ap(reward),
                    db_ap(base_rate),
                    0,
                    note,
                ),
            )
            task_id = int(cur.lastrowid)
            self._insert_transaction(
                conn,
                reward,
                "task_reward",
                f"Выполнена задача: {self._format_task_name(category, item_title)}",
                task_id=task_id,
            )
            retro_details = self._apply_retroactive_indexing(
                conn,
                exclude_task_id=task_id,
            )
            retro_bonus = sum(
                (detail.delta for detail in retro_details),
                Decimal("0.000"),
            )
            return TaskResult(task_id, reward, parse_ap(retro_bonus), retro_details)

    def buy_core(self) -> UpgradeResult:
        with self._connect() as conn:
            quote = self._quote_core(conn)
            result = self._buy_upgrade(
                conn,
                upgrade_type="core",
                target="Ядро Вычислений",
                level_before=db_ap(quote.level_before),
                level_after=db_ap(quote.level_after),
                cost=quote.full_cost,
                cashback_eligible=True,
            )
            self._set_meta(conn, "base_rate", db_ap(quote.level_after))
            return result

    def quote_core_upgrade(self) -> UpgradeQuote:
        with self._connect() as conn:
            return self._quote_core(conn)

    def buy_vector(self, vector: str) -> UpgradeResult:
        vector_info = require_vector(vector)
        with self._connect() as conn:
            quote = self._quote_vector(conn, vector_info.key)
            if quote.maxed:
                raise EconomyError(f"Vector {vector_info.title} is already maxed")
            result = self._buy_upgrade(
                conn,
                upgrade_type="vector",
                target=vector_info.key,
                level_before=str(quote.level_before),
                level_after=str(quote.level_after),
                cost=quote.full_cost,
                cashback_eligible=True,
            )
            self._set_meta(conn, f"vector_level:{vector_info.key}", str(quote.level_after))
            return result

    def quote_vector_upgrade(self, vector: str) -> UpgradeQuote:
        vector_info = require_vector(vector)
        with self._connect() as conn:
            return self._quote_vector(conn, vector_info.key)

    def quote_vector_upgrades(self) -> dict[str, UpgradeQuote]:
        with self._connect() as conn:
            return {key: self._quote_vector(conn, key) for key in VECTORS}

    def estimate_upgrade_spend(
        self,
    ) -> UpgradeSpendEstimate:
        with self._connect() as conn:
            return self._estimate_upgrade_spend(conn)

    def get_earnings_stats(self) -> EarningsStats:
        with self._connect() as conn:
            upgrade_spend = self._estimate_upgrade_spend(conn)
            balance = self._balance(conn)
            starting_balance = self._get_decimal(conn, "historical_starting_balance")
            task_earned = self._task_reward_earned(conn)
            retro_earned = self._task_retro_earned(conn)
            discount_gross = (
                self._get_decimal(conn, "historical_discount_cashback")
                if self._get_int(conn, "cashback_level")
                else Decimal("0.000")
            )
            total_earned = parse_ap(
                balance + upgrade_spend.total_spent + discount_gross
            )
            discount_net = (
                parse_ap(discount_gross - upgrade_spend.discount_spent)
                if discount_gross
                else Decimal("0.000")
            )
            premium_earned = self._task_premium_earned(conn)
            premium_and_other = parse_ap(
                total_earned - task_earned - retro_earned - discount_gross
                - starting_balance
            )
            other_earned = parse_ap(premium_and_other - premium_earned)
            return EarningsStats(
                total_earned=total_earned,
                starting_balance=starting_balance,
                task_earned=task_earned,
                retro_earned=retro_earned,
                discount_gross=discount_gross,
                discount_net=discount_net,
                premium_earned=premium_earned,
                other_earned=other_earned,
                premium_and_other_earned=premium_and_other,
            )

    def set_upgrade_history_settings(
        self,
        *,
        discount_start_base: Decimal | int | str | None = None,
        discount_purchase_cost: Decimal | int | str | None = None,
        historical_discount_cashback: Decimal | int | str | None = None,
        retroactive_indexing_purchase_cost: Decimal | int | str | None = None,
        historical_starting_balance: Decimal | int | str | None = None,
    ) -> None:
        with self._connect() as conn:
            if discount_start_base is not None:
                self._set_meta(
                    conn,
                    "discount_start_base",
                    db_ap(discount_start_base),
                )
            if discount_purchase_cost is not None:
                self._set_meta(
                    conn,
                    "discount_purchase_cost",
                    db_ap(discount_purchase_cost),
                )
            if historical_discount_cashback is not None:
                self._set_meta(
                    conn,
                    "historical_discount_cashback",
                    db_ap(historical_discount_cashback),
                )
            if retroactive_indexing_purchase_cost is not None:
                self._set_meta(
                    conn,
                    "retroactive_indexing_purchase_cost",
                    db_ap(retroactive_indexing_purchase_cost),
                )
            if historical_starting_balance is not None:
                self._set_meta(
                    conn,
                    "historical_starting_balance",
                    db_ap(historical_starting_balance),
                )

    def buy_cashback(self) -> UpgradeResult:
        with self._connect() as conn:
            current_level = self._get_int(conn, "cashback_level")
            if current_level >= CASHBACK_MAX_LEVEL:
                raise EconomyError("Cashback is already maxed")
            new_level = current_level + 1
            result = self._buy_upgrade(
                conn,
                upgrade_type="cashback",
                target="Скидка Терминала",
                level_before=str(current_level),
                level_after=str(new_level),
                cost=CASHBACK_PURCHASE_COST,
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

    def list_tasks(
        self, *, limit: int = 20, premium_pending: bool = False
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            where = "WHERE premium_received = 0" if premium_pending else ""
            rows = conn.execute(
                f"""
                SELECT id, created_at, category, title, vector, units, base_rate,
                    vector_level, vector_multiplier, priority_multiplier,
                    full_close_bonus, catalog_weight, reward, current_reward,
                    retro_paid_base_rate, premium_received, note
                FROM tasks
                {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_task_premium_received(self, task_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, category, title, premium_received FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise EconomyError(f"Task not found: {task_id}")
            if int(row["premium_received"]):
                raise EconomyError(f"Premium is already received for task #{task_id}")
            conn.execute(
                "UPDATE tasks SET premium_received = 1 WHERE id = ?",
                (task_id,),
            )
            updated = conn.execute(
                """
                SELECT id, created_at, category, title, vector, units, reward, current_reward,
                    premium_received
                FROM tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
            return dict(updated)

    def list_categories(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            self._sync_task_categories(conn)
            rows = conn.execute(
                """
                SELECT c.category, c.completed, t.id, t.premium_received,
                    t.reward
                FROM task_categories c
                LEFT JOIN tasks t ON t.category = c.category
                ORDER BY c.completed ASC, c.category COLLATE NOCASE ASC
                """
            ).fetchall()
        categories: dict[str, dict[str, Any]] = {}
        for row in rows:
            category = str(row["category"])
            item = categories.setdefault(
                category,
                {
                    "category": category,
                    "completed": int(row["completed"]),
                    "task_count": 0,
                    "premium_pending_count": 0,
                    "reward_total": Decimal("0.000"),
                    "reward_parts": {},
                    "premium_pending_reward_total": Decimal("0.000"),
                    "premium_total": Decimal("0.000"),
                    "premium_pending_total": Decimal("0.000"),
                },
            )
            if row["id"] is None:
                continue
            item["task_count"] += 1
            if not int(row["premium_received"]):
                item["premium_pending_count"] += 1
                item["premium_pending_reward_total"] = parse_ap(
                    item["premium_pending_reward_total"] + parse_ap(row["reward"])
                )
            item["reward_total"] = parse_ap(
                item["reward_total"] + parse_ap(row["reward"])
            )
            reward = parse_ap(row["reward"])
            item["reward_parts"][reward] = item["reward_parts"].get(reward, 0) + 1
        for item in categories.values():
            item["premium_total"] = parse_ap(item["reward_total"] * Decimal("0.5"))
            item["premium_pending_total"] = parse_ap(
                item["premium_pending_reward_total"] * Decimal("0.5")
            )
            item["reward_formula"] = self._format_reward_formula(
                item["reward_parts"],
                item["reward_total"],
            )
            del item["reward_parts"]
            del item["premium_pending_reward_total"]
        return list(categories.values())

    def set_category_completed(self, category: str, completed: bool = True) -> dict[str, Any]:
        category = category.strip()
        if not category:
            raise ValueError("category must not be empty")
        with self._connect() as conn:
            task_rows = conn.execute(
                "SELECT id, reward, premium_received FROM tasks WHERE category = ?",
                (category,),
            ).fetchall()
            if not task_rows:
                raise EconomyError(f"Category not found: {category}")
            premium_awarded = Decimal("0.000")
            premium_task_count = 0
            if completed:
                pending_rows = [
                    row for row in task_rows if not int(row["premium_received"])
                ]
                premium_task_count = len(pending_rows)
                pending_reward = sum(
                    (parse_ap(row["reward"]) for row in pending_rows),
                    Decimal("0.000"),
                )
                premium_awarded = parse_ap(pending_reward * Decimal("0.5"))
                if premium_awarded:
                    self._insert_transaction(
                        conn,
                        premium_awarded,
                        "category_premium",
                        f"Премия за категорию: {category}",
                    )
                if pending_rows:
                    conn.execute(
                        """
                        UPDATE tasks
                        SET premium_received = 1
                        WHERE category = ? AND premium_received = 0
                        """,
                        (category,),
                    )
            conn.execute(
                """
                INSERT INTO task_categories (category, completed)
                VALUES (?, ?)
                ON CONFLICT(category) DO UPDATE SET completed = excluded.completed
                """,
                (category, 1 if completed else 0),
            )
            row = conn.execute(
                """
                SELECT category, completed
                FROM task_categories
                WHERE category = ?
                """,
                (category,),
            ).fetchone()
            result = dict(row)
            result["premium_awarded"] = premium_awarded
            result["premium_task_count"] = premium_task_count
            return result

    def list_upgrades(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, upgrade_type, target, level_before,
                    level_after, cost, cashback AS discount, note
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
        discount = self._discount(conn, cost, cashback_eligible=cashback_eligible)
        final_cost = parse_ap(cost - discount)
        note = f"{target}: {level_before} -> {level_after}"
        if discount:
            note += f" со скидкой {cashback_level * 5}%"
        self._ensure_can_spend(conn, final_cost)
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
                db_ap(final_cost),
                db_ap(discount),
                note,
            ),
        )
        upgrade_id = int(cur.lastrowid)
        self._insert_transaction(
            conn,
            -final_cost,
            "upgrade_purchase",
            f"Покупка улучшения: {note}",
            upgrade_id=upgrade_id,
        )
        return UpgradeResult(upgrade_id, upgrade_type, target, final_cost, discount)

    def _quote_core(self, conn: sqlite3.Connection) -> UpgradeQuote:
        current_base = self._get_decimal(conn, "base_rate")
        new_base = parse_ap(current_base + CORE_STEP)
        full_cost = parse_ap(current_base * Decimal("8"))
        discount = self._discount(conn, full_cost, cashback_eligible=True)
        return UpgradeQuote(
            target="core",
            level_before=current_base,
            level_after=new_base,
            full_cost=full_cost,
            discount=discount,
            final_cost=parse_ap(full_cost - discount),
        )

    def _quote_vector(self, conn: sqlite3.Connection, vector: str) -> UpgradeQuote:
        vector_info = require_vector(vector)
        current_level = self._get_int(conn, f"vector_level:{vector_info.key}")
        if current_level >= VECTOR_MAX_LEVEL:
            return UpgradeQuote(
                target=vector_info.key,
                level_before=current_level,
                level_after=current_level,
                full_cost=Decimal("0.000"),
                discount=Decimal("0.000"),
                final_cost=Decimal("0.000"),
                maxed=True,
            )
        new_level = current_level + 1
        full_cost = parse_ap(Decimal(new_level) * Decimal("0.5"))
        discount = self._discount(conn, full_cost, cashback_eligible=True)
        return UpgradeQuote(
            target=vector_info.key,
            level_before=current_level,
            level_after=new_level,
            full_cost=full_cost,
            discount=discount,
            final_cost=parse_ap(full_cost - discount),
        )

    def _discount(
        self,
        conn: sqlite3.Connection,
        cost: Decimal,
        *,
        cashback_eligible: bool,
    ) -> Decimal:
        cashback_level = self._get_int(conn, "cashback_level")
        return (
            parse_ap(cost * Decimal(cashback_level) * Decimal("0.05"))
            if cashback_eligible and cashback_level
            else Decimal("0.000")
        )

    def _estimate_upgrade_spend(self, conn: sqlite3.Connection) -> UpgradeSpendEstimate:
        state = EconomyState(
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
        discount_start_base = self._get_decimal(conn, "discount_start_base")
        discount_purchase_cost = self._get_decimal(conn, "discount_purchase_cost")
        retroactive_indexing_purchase_cost = self._get_decimal(
            conn, "retroactive_indexing_purchase_cost"
        )
        discount_rate = Decimal(state.cashback_level) * Decimal("0.05")

        core_spent = Decimal("0.000")
        discount_saved = Decimal("0.000")
        base = DEFAULT_BASE_RATE
        while base < state.base_rate:
            full_cost = parse_ap(base * Decimal("8"))
            if base >= discount_start_base:
                core_spent += parse_ap(full_cost * (Decimal("1") - discount_rate))
                discount_saved += parse_ap(full_cost * discount_rate)
            else:
                core_spent += full_cost
            base = parse_ap(base + CORE_STEP)

        vector_spent_by_key = {}
        for key, level in state.vector_levels.items():
            spent = Decimal("0.000")
            for next_level in range(1, level + 1):
                spent += parse_ap(Decimal(next_level) * Decimal("0.5"))
            vector_spent_by_key[key] = parse_ap(spent)

        core_spent = parse_ap(core_spent)
        discount_saved = parse_ap(discount_saved)
        vector_spent = parse_ap(sum(vector_spent_by_key.values(), Decimal("0.000")))
        discount_spent = discount_purchase_cost if state.cashback_level else Decimal("0.000")
        retroactive_indexing_spent = (
            retroactive_indexing_purchase_cost
            if state.retroactive_indexing_enabled
            else Decimal("0.000")
        )
        return UpgradeSpendEstimate(
            core_spent=core_spent,
            vector_spent=vector_spent,
            discount_spent=discount_spent,
            discount_saved=discount_saved,
            retroactive_indexing_spent=retroactive_indexing_spent,
            vector_spent_by_key=vector_spent_by_key,
            total_spent=parse_ap(
                discount_spent
                + retroactive_indexing_spent
                + core_spent
                + vector_spent
            ),
            discount_start_base=parse_ap(discount_start_base),
        )

    def _apply_retroactive_indexing(
        self, conn: sqlite3.Connection, *, exclude_task_id: int
    ) -> list[RetroBonusDetail]:
        if not self._get_bool(conn, "retroactive_indexing_enabled"):
            return []
        current_base = self._get_decimal(conn, "base_rate")
        rows = conn.execute(
            """
            SELECT id, category, title, units, vector_multiplier, priority_multiplier,
                full_close_bonus, catalog_weight, current_reward,
                retro_paid_base_rate
            FROM tasks
            WHERE id != ?
            ORDER BY id ASC
            """,
            (exclude_task_id,),
        ).fetchall()
        total = Decimal("0.000")
        details: list[RetroBonusDetail] = []
        for row in rows:
            paid_base = parse_ap(row["retro_paid_base_rate"])
            if paid_base >= current_base:
                continue
            previous_reward = parse_ap(row["current_reward"])
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
            updated_reward = parse_ap(previous_reward + delta)
            details.append(
                RetroBonusDetail(
                    task_id=int(row["id"]),
                    title=self._format_task_name(
                        str(row["category"] or ""),
                        str(row["title"]),
                    ),
                    units=int(row["units"]),
                    paid_base_rate=paid_base,
                    current_base_rate=current_base,
                    vector_multiplier=parse_ap(row["vector_multiplier"]),
                    priority_multiplier=parse_ap(row["priority_multiplier"]),
                    full_close_bonus=parse_ap(row["full_close_bonus"]),
                    catalog_weight=parse_ap(row["catalog_weight"]),
                    previous_reward=previous_reward,
                    updated_reward=updated_reward,
                    delta=delta,
                )
            )
            conn.execute(
                """
                UPDATE tasks
                SET retro_paid_base_rate = ?, current_reward = ?
                WHERE id = ?
                """,
                (
                    db_ap(current_base),
                    db_ap(updated_reward),
                    row["id"],
                ),
            )
        if total:
            self._insert_transaction(
                conn,
                total,
                "retro_bonus",
                f"Ретроспективная индексация по задачам: {len(details)}",
            )
        return details

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

    def _split_task_title(self, title: str) -> tuple[str, str]:
        if ":" not in title:
            return "", title.strip()
        category, task_title = title.split(":", 1)
        category = category.strip()
        task_title = task_title.strip()
        if not category or not task_title:
            return "", title.strip()
        return category, task_title

    def _format_task_name(self, category: str, title: str) -> str:
        category = category.strip()
        title = title.strip()
        return f"{category}: {title}" if category else title

    def _format_reward_formula(self, parts: dict[Decimal, int], total: Decimal) -> str:
        terms = []
        for reward, count in sorted(parts.items()):
            if count == 1:
                terms.append(db_ap(reward).rstrip("0").rstrip("."))
            else:
                terms.append(f"{db_ap(reward).rstrip('0').rstrip('.')}x{count}")
        return f"{' + '.join(terms)} = {db_ap(total).rstrip('0').rstrip('.')}"

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

    def _sum_transactions(self, conn: sqlite3.Connection, kind: str) -> Decimal:
        rows = conn.execute(
            "SELECT amount FROM transactions WHERE kind = ?",
            (kind,),
        ).fetchall()
        total = sum((parse_ap(row["amount"]) for row in rows), Decimal("0.000"))
        return parse_ap(total)

    def _task_reward_earned(self, conn: sqlite3.Connection) -> Decimal:
        rows = conn.execute("SELECT reward FROM tasks").fetchall()
        if not rows:
            return self._sum_transactions(conn, "task_reward")
        total = sum((parse_ap(row["reward"]) for row in rows), Decimal("0.000"))
        return parse_ap(total)

    def _task_retro_earned(self, conn: sqlite3.Connection) -> Decimal:
        rows = conn.execute("SELECT reward, current_reward FROM tasks").fetchall()
        if not rows:
            return self._sum_transactions(conn, "retro_bonus")
        total = sum(
            (
                parse_ap(row["current_reward"] or row["reward"])
                - parse_ap(row["reward"])
                for row in rows
            ),
            Decimal("0.000"),
        )
        return parse_ap(total)

    def _task_premium_earned(self, conn: sqlite3.Connection) -> Decimal:
        rows = conn.execute(
            "SELECT reward FROM tasks WHERE premium_received = 1"
        ).fetchall()
        reward_total = sum(
            (parse_ap(row["reward"]) for row in rows),
            Decimal("0.000"),
        )
        return parse_ap(reward_total * Decimal("0.5"))

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
                category TEXT NOT NULL DEFAULT '',
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
                current_reward TEXT NOT NULL,
                retro_paid_base_rate TEXT NOT NULL,
                premium_received INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS task_categories (
                category TEXT PRIMARY KEY,
                completed INTEGER NOT NULL DEFAULT 0
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
        self._migrate_schema(conn)

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        task_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        if "current_reward" not in task_columns:
            self._add_column_if_missing(
                conn,
                "tasks",
                "current_reward",
                "ALTER TABLE tasks ADD COLUMN current_reward TEXT",
            )
            conn.execute("UPDATE tasks SET current_reward = reward")
        if "premium_received" not in task_columns:
            self._add_column_if_missing(
                conn,
                "tasks",
                "premium_received",
                "ALTER TABLE tasks ADD COLUMN premium_received INTEGER NOT NULL DEFAULT 0",
            )
        if "category" not in task_columns:
            self._add_column_if_missing(
                conn,
                "tasks",
                "category",
                "ALTER TABLE tasks ADD COLUMN category TEXT NOT NULL DEFAULT ''",
            )
        self._backfill_task_categories(conn)
        self._sync_task_categories(conn)

    def _backfill_task_categories(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT id, title
            FROM tasks
            WHERE category = '' AND instr(title, ':') > 0
            """
        ).fetchall()
        for row in rows:
            category, title = self._split_task_title(str(row["title"]))
            if not category:
                continue
            conn.execute(
                "UPDATE tasks SET category = ?, title = ? WHERE id = ?",
                (category, title, row["id"]),
            )

    def _sync_task_categories(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO task_categories (category, completed)
            SELECT DISTINCT category, 0
            FROM tasks
            WHERE category != ''
            """
        )

    def _add_column_if_missing(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        statement: str,
    ) -> None:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
            columns = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            if column not in columns:
                raise

    def _ensure_defaults(self, conn: sqlite3.Connection) -> None:
        defaults = {
            "schema_version": "1",
            "base_rate": db_ap(DEFAULT_BASE_RATE),
            "cashback_level": "0",
            "retroactive_indexing_enabled": "0",
            "discount_start_base": db_ap(DEFAULT_DISCOUNT_START_BASE),
            "discount_purchase_cost": db_ap(DEFAULT_DISCOUNT_PURCHASE_COST),
            "historical_discount_cashback": db_ap(DEFAULT_HISTORICAL_DISCOUNT_CASHBACK),
            "retroactive_indexing_purchase_cost": db_ap(
                DEFAULT_HISTORICAL_RETROACTIVE_INDEXING_COST
            ),
            "historical_starting_balance": db_ap(DEFAULT_HISTORICAL_STARTING_BALANCE),
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
