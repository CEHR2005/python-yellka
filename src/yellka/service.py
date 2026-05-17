from __future__ import annotations

import sqlite3
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .catalog import UPGRADABLE_VECTORS, VECTORS, find_catalog_item, require_vector
from .money import db_ap, format_ap, parse_ap
from .shop_catalog import (
    AP,
    NEURAL_SHARD,
    SHADOW_AP,
    SHOP_ITEMS,
    SINGULARITY_SHARD,
    require_shop_item,
)

DEFAULT_BASE_RATE = Decimal("0.200")
CORE_STEP = Decimal("0.050")
CORE_COST_MULTIPLIER = Decimal("10.000")
FULL_CLOSE_MULTIPLIER = Decimal("1.500")
PRIORITY_MULTIPLIER = Decimal("2.000")
VECTOR_STEP = Decimal("0.100")
VECTOR_MAX_LEVEL = 10
CASHBACK_MAX_LEVEL = 5
CASHBACK_PURCHASE_BASE_COST = Decimal("3.000")
PRIME_UPKEEP_DISCOUNT_RATE = Decimal("0.250")
SR_UPKEEP = Decimal("3.000")
SR_REQUIRED_DOMINANT_LEVEL = 4
RETROACTIVE_INDEXING_COST = Decimal("25.000")
RETRO_BUFFER_BASE_LIMIT = 10
RETRO_BUFFER_BASE_COMMISSION = Decimal("0.300")
RETRO_BUFFER_MIN_FEE = Decimal("1.000")
UPDATE_BONUS = Decimal("2.500")
DOMINANT_UPGRADE_COST = Decimal("3.000")
DEFAULT_DISCOUNT_START_BASE = Decimal("0.800")
DEFAULT_DISCOUNT_PURCHASE_COST = Decimal("15.000")
DEFAULT_HISTORICAL_DISCOUNT_CASHBACK = Decimal("18.270")
DEFAULT_HISTORICAL_RETROACTIVE_INDEXING_COST = Decimal("20.000")
DEFAULT_HISTORICAL_STARTING_BALANCE = Decimal("0.000")
DEFAULT_HISTORICAL_RETRO_BONUS = Decimal("0.000")
TRACKER_STATUS_DRAFT = "draft"
TRACKER_STATUS_DONE = "done"
TRACKER_STATUS_SUBMITTED = "submitted"
TRACKER_STATUSES = {
    TRACKER_STATUS_DRAFT,
    TRACKER_STATUS_DONE,
    TRACKER_STATUS_SUBMITTED,
}


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
class RetroBufferQuote:
    eligible_count: int
    limit: int
    gross: Decimal
    fee: Decimal
    net: Decimal
    commission_rate: Decimal
    activation_allowed: bool


@dataclass(frozen=True)
class ShopQuote:
    item_key: str
    title: str
    section: str
    target: str
    quantity: int
    currency: str
    full_cost: Decimal
    discount: Decimal
    final_cost: Decimal
    available: bool
    reason: str = ""
    metadata: dict[str, Any] | None = None


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


@dataclass(frozen=True)
class CrewEffects:
    base_rate_bonus: Decimal
    vector_bonus: dict[str, Decimal]
    full_close_bonus: Decimal
    shop_flat_discount: dict[str, Decimal]
    shop_percent_discount: dict[str, Decimal]
    vector_upgrade_discount: Decimal
    free_shop_items: frozenset[str]


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
                    key: self._get_int(conn, f"vector_level:{key}")
                    for key in UPGRADABLE_VECTORS
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
            crew_effects = self._active_crew_effects(conn)
            effective_base_rate = parse_ap(base_rate + crew_effects.base_rate_bonus)
            if vector_info.key == "media":
                vector_level = 0
                vector_multiplier = Decimal("2.000")
            else:
                vector_level = self._get_int(conn, f"vector_level:{vector_info.key}")
                vector_multiplier = parse_ap(Decimal("1") + VECTOR_STEP * vector_level)
            crew_vector_bonus = crew_effects.vector_bonus.get(
                vector_info.key,
                Decimal("0.000"),
            )
            vector_multiplier = parse_ap(vector_multiplier + crew_vector_bonus)
            priority_multiplier = PRIORITY_MULTIPLIER if priority else Decimal("1.000")
            full_close_bonus = (
                parse_ap(FULL_CLOSE_MULTIPLIER + crew_effects.full_close_bonus)
                if full_close
                else Decimal("1.000")
            )
            task_multiplier = parse_ap(
                vector_multiplier * priority_multiplier * full_close_bonus
            )
            reward = parse_ap(
                Decimal(units)
                * effective_base_rate
                * task_weight
                * task_multiplier
            )

            now = self._now()
            cur = conn.execute(
                """
                INSERT INTO tasks (
                    created_at, category, title, vector, units, base_rate, vector_level,
                    vector_multiplier, priority_multiplier, full_close_bonus,
                    catalog_weight, crew_vector_bonus, reward, current_reward,
                    retro_paid_base_rate, premium_received, note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    category,
                    item_title,
                    vector_info.key,
                    units,
                    db_ap(effective_base_rate),
                    vector_level,
                    db_ap(vector_multiplier),
                    db_ap(priority_multiplier),
                    db_ap(full_close_bonus),
                    db_ap(task_weight),
                    db_ap(crew_vector_bonus),
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
            return TaskResult(task_id, reward, Decimal("0.000"), [])

    def buy_core(self) -> UpgradeResult:
        with self._connect() as conn:
            quote = self._quote_core(conn)
            shadow_base_after = None
            if self._get_shop_level(conn, "noctur.core_rewrite"):
                shadow_base_after = parse_ap(
                    self._core_shadow_base(conn, parse_ap(quote.level_before)) + CORE_STEP
                )
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
            if shadow_base_after is not None:
                self._set_meta(conn, "core_shadow_base", db_ap(shadow_base_after))
            return result

    def quote_core_upgrade(self) -> UpgradeQuote:
        with self._connect() as conn:
            return self._quote_core(conn)

    def buy_vector(self, vector: str) -> UpgradeResult:
        vector_info = require_vector(vector)
        if vector_info.key not in UPGRADABLE_VECTORS:
            raise EconomyError(f"Vector {vector_info.title} is not upgradeable")
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
                extra_discount_rate=self._active_crew_effects(conn).vector_upgrade_discount,
            )
            self._set_meta(conn, f"vector_level:{vector_info.key}", str(quote.level_after))
            self._apply_cascade_resonance(conn, vector_info.key)
            return result

    def quote_vector_upgrade(self, vector: str) -> UpgradeQuote:
        vector_info = require_vector(vector)
        if vector_info.key not in UPGRADABLE_VECTORS:
            raise EconomyError(f"Vector {vector_info.title} is not upgradeable")
        with self._connect() as conn:
            return self._quote_vector(conn, vector_info.key)

    def quote_vector_upgrades(self) -> dict[str, UpgradeQuote]:
        with self._connect() as conn:
            return {key: self._quote_vector(conn, key) for key in UPGRADABLE_VECTORS}

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

    def earned_ap_timeline(self, *, limit: int = 80) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, amount, kind, note
                FROM transactions
                WHERE currency = ?
                ORDER BY id ASC
                """,
                (AP,),
            ).fetchall()
        points: list[dict[str, Any]] = []
        cumulative = Decimal("0.000")
        for row in rows:
            amount = parse_ap(row["amount"])
            if amount <= 0:
                continue
            cumulative = parse_ap(cumulative + amount)
            points.append(
                {
                    "id": int(row["id"]),
                    "created_at": row["created_at"],
                    "amount": db_ap(amount),
                    "cumulative": db_ap(cumulative),
                    "kind": row["kind"],
                    "note": row["note"],
                }
            )
        if limit > 0:
            points = points[-limit:]
        return {
            "event_count": len(points),
            "total": db_ap(cumulative),
            "points": points,
        }

    def quote_upgrade_efficiency(self, vector: str = "code") -> dict[str, Any]:
        vector_info = require_vector(vector)
        if vector_info.key not in UPGRADABLE_VECTORS:
            raise EconomyError(f"Vector {vector_info.title} is not upgradeable")
        with self._connect() as conn:
            crew_effects = self._active_crew_effects(conn)
            base_rate = parse_ap(
                self._get_decimal(conn, "base_rate") + crew_effects.base_rate_bonus
            )
            entries: list[dict[str, Any]] = []

            core_quote = self._quote_core(conn)
            vector_multiplier = self._current_vector_multiplier(
                conn,
                vector_info.key,
                crew_effects,
            )
            core_step = parse_ap(core_quote.level_after - core_quote.level_before)
            core_impact = parse_ap(core_step * vector_multiplier)
            entries.append(
                self._upgrade_efficiency_entry(
                    kind="core",
                    target="Ядро",
                    cost=core_quote.final_cost,
                    impact=core_impact,
                    maxed=core_quote.maxed,
                )
            )

            vector_quote = self._quote_vector(conn, vector_info.key)
            vector_impact = (
                parse_ap(VECTOR_STEP * base_rate)
                if not vector_quote.maxed
                else Decimal("0.000")
            )
            entries.append(
                self._upgrade_efficiency_entry(
                    kind="vector",
                    target=vector_info.key,
                    cost=vector_quote.final_cost,
                    impact=vector_impact,
                    maxed=vector_quote.maxed,
                )
            )

        entries.sort(
            key=lambda entry: (
                Decimal(str(entry["impact_per_ap"])),
                Decimal(str(entry["impact"])),
            ),
            reverse=True,
        )
        return {
            "vector": vector_info.key,
            "units": 1,
            "catalog_weight": "1.000",
            "base_rate": db_ap(base_rate),
            "vector_multiplier": db_ap(vector_multiplier),
            "core_step": db_ap(core_step),
            "entries": entries,
        }

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
            cost = self._cashback_cost(current_level)
            result = self._buy_upgrade(
                conn,
                upgrade_type="cashback",
                target="Скидка Терминала",
                level_before=str(current_level),
                level_after=str(new_level),
                cost=cost,
                cashback_eligible=False,
            )
            self._set_meta(conn, "cashback_level", str(new_level))
            return result

    def buy_retroactive_indexing(self) -> UpgradeResult:
        with self._connect() as conn:
            return self._activate_retro_buffer(conn)

    def get_wallet(self) -> dict[str, Any]:
        with self._connect() as conn:
            return {
                "currencies": {
                    AP: db_ap(self._balance(conn, AP)),
                    SHADOW_AP: db_ap(self._balance(conn, SHADOW_AP)),
                    SINGULARITY_SHARD: db_ap(self._balance(conn, SINGULARITY_SHARD)),
                    NEURAL_SHARD: db_ap(self._balance(conn, NEURAL_SHARD)),
                },
                "base_rate": db_ap(self._get_decimal(conn, "base_rate")),
                "cashback_level": self._get_int(conn, "cashback_level"),
                "cashback_percent": self._discount_rate(conn, cashback_eligible=True) * 100,
                "vector_levels": {
                    key: self._get_int(conn, f"vector_level:{key}")
                    for key in UPGRADABLE_VECTORS
                },
            }

    def list_shop_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "key": item.key,
                "title": item.title,
                "section": item.section,
                "currency": item.currency,
                "base_cost": db_ap(item.base_cost),
                "cost_formula": item.cost_formula,
                "max_level": item.max_level,
                "discount_tags": list(item.discount_tags),
                "gate": item.gate,
                "effect_kind": item.effect_kind,
                "description": item.description,
            }
            for item in SHOP_ITEMS
        ]

    def quote_shop_purchase(
        self,
        item_key: str,
        *,
        target: str = "",
        quantity: int = 1,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            return self._shop_quote_payload(
                self._quote_shop_purchase(conn, item_key, target=target, quantity=quantity, options=options)
            )

    def buy_shop_item(
        self,
        item_key: str,
        *,
        target: str = "",
        quantity: int = 1,
        note: str = "",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if item_key == "terminal.core":
            result = self.buy_core()
            return self._record_terminal_purchase(item_key, result, quantity, note)
        if item_key == "terminal.vector":
            if not target:
                raise ValueError("target is required for vector upgrades")
            result = self.buy_vector(target)
            return self._record_terminal_purchase(item_key, result, quantity, note)
        if item_key == "terminal.cashback":
            result = self.buy_cashback()
            return self._record_terminal_purchase(item_key, result, quantity, note)
        if item_key == "terminal.retro_buffer":
            result = self.buy_retroactive_indexing()
            return self._record_terminal_purchase(item_key, result, quantity, note)

        with self._connect() as conn:
            quote = self._quote_shop_purchase(
                conn,
                item_key,
                target=target,
                quantity=quantity,
                options=options,
            )
            if not quote.available:
                raise EconomyError(quote.reason or "Purchase is not available")
            purchase_id = self._insert_shop_purchase(conn, quote, note)
            self._spend_currency(
                conn,
                quote.final_cost,
                quote.currency,
                f"Shop purchase: {quote.title}",
                purchase_id=purchase_id,
            )
            self._apply_shop_effect(conn, quote, purchase_id, note)
            return self._shop_purchase_dict(
                conn.execute(
                    "SELECT * FROM shop_purchases WHERE id = ?",
                    (purchase_id,),
                ).fetchone()
            )

    def list_shop_purchases(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM shop_purchases
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._shop_purchase_dict(row) for row in rows]

    def list_history_entries(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            purchases = [
                self._history_purchase_entry(row)
                for row in conn.execute(
                    """
                    SELECT *
                    FROM shop_purchases
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            ]
            task_submissions = [
                self._history_task_submission_entry(row)
                for row in conn.execute(
                    """
                    SELECT id, created_at, updated_at, category, title,
                        economy_task_id, submitted_reward, submitted_retro_bonus
                    FROM tracker_tasks
                    WHERE status = ? AND economy_task_id IS NOT NULL
                    ORDER BY updated_at DESC, id DESC
                    LIMIT ?
                    """,
                    (TRACKER_STATUS_SUBMITTED, limit),
                ).fetchall()
            ]
        entries = purchases + task_submissions
        entries.sort(key=lambda item: str(item["created_at"]), reverse=True)
        return entries[:limit]

    def get_shop_purchase(self, purchase_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM shop_purchases WHERE id = ?",
                (purchase_id,),
            ).fetchone()
        if row is None:
            raise EconomyError(f"Shop purchase not found: {purchase_id}")
        return self._shop_purchase_dict(row)

    def quote_retro_buffer(self) -> RetroBufferQuote:
        with self._connect() as conn:
            return self._quote_retro_buffer(conn)

    def get_retro_buffer(self) -> dict[str, Any]:
        with self._connect() as conn:
            return self._retro_buffer_payload(conn)

    def list_effects(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM active_effects ORDER BY key COLLATE NOCASE ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def prime_status(self) -> dict[str, Any]:
        with self._connect() as conn:
            return {
                "active": self._get_bool(conn, "prime_active"),
                "active_since": self._get_meta(conn, "prime_active_since"),
                "weeks_purchased": self._get_int(conn, "prime_weeks_purchased"),
                "loyalty_weeks": self._get_int(conn, "prime_loyalty_weeks"),
            }

    def crew_upkeep_summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM cabins WHERE active = 1").fetchall()
            discount_rate = self._crew_upkeep_discount_rate(conn)
            base_total = Decimal("0.000")
            discount_total = Decimal("0.000")
            effective_total = Decimal("0.000")
            for row in rows:
                upkeep = self._cabin_upkeep(row, discount_rate)
                base_total = parse_ap(base_total + upkeep["base"])
                discount_total = parse_ap(discount_total + upkeep["discount"])
                effective_total = parse_ap(effective_total + upkeep["effective"])
            return {
                "active_count": len(rows),
                "base_total": db_ap(base_total),
                "discount_total": db_ap(discount_total),
                "effective_total": db_ap(effective_total),
                "discount_rate": db_ap(discount_rate),
                "prime_active": self._get_bool(conn, "prime_active"),
            }

    def list_expeditions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM expeditions ORDER BY id DESC").fetchall()
        return [dict(row) for row in rows]

    def create_expedition(
        self,
        *,
        title: str,
        difficulty: str = "normal",
        note: str = "",
    ) -> dict[str, Any]:
        title = title.strip()
        if not title:
            raise ValueError("title must not be empty")
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO expeditions (created_at, title, status, difficulty, note)
                VALUES (?, ?, 'planned', ?, ?)
                """,
                (self._now(), title, difficulty.strip(), note.strip()),
            )
            return dict(conn.execute("SELECT * FROM expeditions WHERE id = ?", (cur.lastrowid,)).fetchone())

    def list_cabins(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM cabins ORDER BY id DESC").fetchall()
            return [self._cabin_dict(conn, row) for row in rows]

    def create_cabin(
        self,
        *,
        name: str,
        rank: str = "C",
        tags: str = "",
        sample_code: str = "",
        universe: str = "",
        full_tags: str = "",
        sedative_dose: Decimal | int | str = "0",
        upkeep: Decimal | int | str = "0",
        subscription_tier: str = "",
        subscription_started_at: str = "",
        recessive_name: str = "",
        recessive_description: str = "",
        dominants: list[dict[str, Any]] | str | None = None,
        active: bool = True,
        note: str = "",
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("name must not be empty")
        active_tags = tags.strip() or full_tags.strip()
        with self._connect() as conn:
            dominants_json = self._dominants_json(
                dominants,
                max_level=self._dominant_max_level(conn, rank),
            )
            cur = conn.execute(
                """
                INSERT INTO cabins (
                    created_at, sample_code, name, universe, rank, tags, full_tags,
                    sedative_dose, upkeep, subscription_tier, subscription_started_at,
                    recessive_name, recessive_description,
                    dominants, active, note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._now(),
                    sample_code.strip(),
                    name,
                    universe.strip(),
                    rank.strip(),
                    active_tags,
                    full_tags.strip(),
                    db_ap(sedative_dose),
                    db_ap(upkeep),
                    subscription_tier.strip(),
                    subscription_started_at.strip(),
                    recessive_name.strip(),
                    recessive_description.strip(),
                    dominants_json,
                    1 if active else 0,
                    note.strip(),
                ),
            )
            row = conn.execute("SELECT * FROM cabins WHERE id = ?", (cur.lastrowid,)).fetchone()
            return self._cabin_dict(conn, row)

    def update_cabin(self, cabin_id: int, **changes: Any) -> dict[str, Any]:
        allowed = {
            "sample_code",
            "name",
            "universe",
            "rank",
            "tags",
            "full_tags",
            "sedative_dose",
            "upkeep",
            "subscription_tier",
            "subscription_started_at",
            "recessive_name",
            "recessive_description",
            "dominants",
            "active",
            "note",
        }
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM cabins WHERE id = ?", (cabin_id,)).fetchone()
            if row is None:
                raise EconomyError(f"Cabin not found: {cabin_id}")
            values: dict[str, Any] = {}
            rank = str(changes.get("rank") if changes.get("rank") is not None else row["rank"])
            for key, value in changes.items():
                if key not in allowed or value is None:
                    continue
                if key in {"sedative_dose", "upkeep"}:
                    values[key] = db_ap(value)
                elif key == "dominants":
                    values[key] = self._dominants_json(
                        value,
                        max_level=self._dominant_max_level(conn, rank),
                    )
                elif key == "active":
                    values[key] = 1 if value else 0
                else:
                    values[key] = str(value).strip()
            if "name" in values and not values["name"]:
                raise ValueError("name must not be empty")
            if not values:
                return self._cabin_dict(conn, row)
            assignments = ", ".join(f"{key} = ?" for key in values)
            conn.execute(
                f"UPDATE cabins SET {assignments} WHERE id = ?",
                (*values.values(), cabin_id),
            )
            row = conn.execute("SELECT * FROM cabins WHERE id = ?", (cabin_id,)).fetchone()
            return self._cabin_dict(conn, row)

    def update_cabin_dominant_level(
        self,
        cabin_id: int,
        dominant: int | str,
        level: int,
    ) -> dict[str, Any]:
        if level < 1:
            raise ValueError("dominant level must be at least 1")
        cabin = self.get_cabin(cabin_id)
        traits = self._dominant_traits(cabin["dominants"])
        if not traits:
            raise EconomyError(f"Cabin #{cabin_id} has no dominant traits")
        if isinstance(dominant, int) or str(dominant).isdigit():
            index = int(dominant) - 1
            if index < 0 or index >= len(traits):
                raise ValueError(f"dominant index must be between 1 and {len(traits)}")
        else:
            target = self._normalize_trait_name(str(dominant))
            index = next(
                (
                    idx
                    for idx, trait in enumerate(traits)
                    if self._normalize_trait_name(trait["name"]) == target
                ),
                -1,
            )
            if index < 0:
                raise EconomyError(f"Dominant trait not found in cabin #{cabin_id}: {dominant}")
        traits[index] = {**traits[index], "level": level}
        return self.update_cabin(cabin_id, dominants=traits)

    def upgrade_cabin_dominant(
        self,
        cabin_id: int,
        dominant: int | str,
    ) -> dict[str, Any]:
        cost = parse_ap(DOMINANT_UPGRADE_COST)
        with self._connect() as conn:
            cabin = conn.execute("SELECT * FROM cabins WHERE id = ?", (cabin_id,)).fetchone()
            if cabin is None:
                raise EconomyError(f"Cabin not found: {cabin_id}")
            traits = self._dominant_traits(cabin["dominants"])
            if not traits:
                raise EconomyError(f"Cabin #{cabin_id} has no dominant traits")
            if isinstance(dominant, int) or str(dominant).isdigit():
                index = int(dominant) - 1
                if index < 0 or index >= len(traits):
                    raise ValueError(f"dominant index must be between 1 and {len(traits)}")
            else:
                target = self._normalize_trait_name(str(dominant))
                index = next(
                    (
                        idx
                        for idx, trait in enumerate(traits)
                        if self._normalize_trait_name(trait["name"]) == target
                    ),
                    -1,
                )
                if index < 0:
                    raise EconomyError(f"Dominant trait not found in cabin #{cabin_id}: {dominant}")

            ap_before = self._balance(conn, AP)
            shadow_before = self._balance(conn, SHADOW_AP)
            self._ensure_can_spend(conn, cost)
            trait = traits[index]
            level_before = int(trait["level"])
            max_level = self._dominant_max_level(conn, str(cabin["rank"]))
            if level_before >= max_level:
                raise EconomyError(
                    f"Dominant trait is already at max level {max_level} for {cabin['rank']} rank"
                )
            level_after = level_before + 1
            traits[index] = {**trait, "level": level_after}
            dominants_json = self._dominants_json(traits, max_level=max_level)
            conn.execute(
                "UPDATE cabins SET dominants = ? WHERE id = ?",
                (dominants_json, cabin_id),
            )
            note = (
                f"Прокачка черты экипажа: {cabin['name']} - "
                f"{trait['name']} Lv.{level_before} -> Lv.{level_after}"
            )
            self._spend_ap_with_shadow(
                conn,
                cost,
                "crew_dominant_upgrade",
                note,
                source="crew",
            )
            updated = self._cabin_dict(
                conn,
                conn.execute("SELECT * FROM cabins WHERE id = ?", (cabin_id,)).fetchone(),
            )
            ap_after = self._balance(conn, AP)
            shadow_after = self._balance(conn, SHADOW_AP)
        updated.update(
            {
                "dominant_name": trait["name"],
                "level_before": level_before,
                "level_after": level_after,
                "upgrade_cost": db_ap(cost),
                "balance_before": db_ap(ap_before),
                "balance_after": db_ap(ap_after),
                "balance_delta": db_ap(ap_after - ap_before),
                "shadow_balance_before": db_ap(shadow_before),
                "shadow_balance_after": db_ap(shadow_after),
                "shadow_balance_delta": db_ap(shadow_after - shadow_before),
            }
        )
        return updated

    def excise_cabin_defect(self, cabin_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            cabin = conn.execute("SELECT * FROM cabins WHERE id = ?", (cabin_id,)).fetchone()
            if cabin is None:
                raise EconomyError(f"Cabin not found: {cabin_id}")
            defect_name = str(cabin["recessive_name"] or "").strip()
            if not defect_name:
                raise EconomyError(f"Cabin #{cabin_id} has no defect to excise")
            quote = self._quote_shop_purchase(
                conn,
                "genesis.defect_excision",
                target=f"cabin:{cabin_id}",
                quantity=1,
                options={},
            )
            if not quote.available:
                raise EconomyError(quote.reason or "Defect excision is not available")
            balance_before = self._balance(conn, quote.currency)
            note = f"Иссечение недостатка: {cabin['name']} - {defect_name}"
            purchase_id = self._insert_shop_purchase(conn, quote, note)
            self._spend_currency(
                conn,
                quote.final_cost,
                quote.currency,
                f"Shop purchase: {quote.title}",
                purchase_id=purchase_id,
            )
            self._apply_shop_effect(conn, quote, purchase_id, note)
            conn.execute(
                """
                UPDATE cabins
                SET recessive_name = '', recessive_description = ''
                WHERE id = ?
                """,
                (cabin_id,),
            )
            updated = self._cabin_dict(
                conn,
                conn.execute("SELECT * FROM cabins WHERE id = ?", (cabin_id,)).fetchone(),
            )
            balance_after = self._balance(conn, quote.currency)
        updated.update(
            {
                "excised_defect": defect_name,
                "excision_cost": db_ap(quote.final_cost),
                "excision_currency": quote.currency,
                "purchase_id": purchase_id,
                "balance_before": db_ap(balance_before),
                "balance_after": db_ap(balance_after),
            }
        )
        return updated

    def promote_cabin_to_sr(self, cabin_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            cabin = conn.execute("SELECT * FROM cabins WHERE id = ?", (cabin_id,)).fetchone()
            if cabin is None:
                raise EconomyError(f"Cabin not found: {cabin_id}")
            quote = self._quote_cabin_sr_promotion(conn, cabin)
            if not quote["available"]:
                raise EconomyError(str(quote["reason"]))
            item = require_shop_item("genesis.rank_change")
            full_cost = parse_ap(SR_UPKEEP)
            final_cost = parse_ap(quote["cost"])
            shop_quote = ShopQuote(
                item.key,
                item.title,
                item.section,
                f"cabin:{cabin_id}",
                1,
                AP,
                full_cost,
                parse_ap(full_cost - final_cost),
                final_cost,
                True,
                "",
                {"rank_before": cabin["rank"], "rank_after": "SR"},
            )
            ap_before = self._balance(conn, AP)
            shadow_before = self._balance(conn, SHADOW_AP)
            note = f"Повышение ранга: {cabin['name']} S -> SR"
            purchase_id = self._insert_shop_purchase(conn, shop_quote, note)
            self._spend_currency(
                conn,
                final_cost,
                AP,
                f"Shop purchase: {shop_quote.title}",
                purchase_id=purchase_id,
            )
            conn.execute(
                """
                UPDATE cabins
                SET rank = 'SR', upkeep = ?, subscription_tier = 'SR',
                    subscription_started_at = ?
                WHERE id = ?
                """,
                (db_ap(SR_UPKEEP), self._now()[:10], cabin_id),
            )
            updated = self._cabin_dict(
                conn,
                conn.execute("SELECT * FROM cabins WHERE id = ?", (cabin_id,)).fetchone(),
            )
            ap_after = self._balance(conn, AP)
            shadow_after = self._balance(conn, SHADOW_AP)
        updated.update(
            {
                "promotion_cost": db_ap(final_cost),
                "promotion_currency": AP,
                "purchase_id": purchase_id,
                "ap_balance_before": db_ap(ap_before),
                "ap_balance_after": db_ap(ap_after),
                "shadow_balance_before": db_ap(shadow_before),
                "shadow_balance_after": db_ap(shadow_after),
            }
        )
        return updated

    def delete_cabin(self, cabin_id: int) -> dict[str, Any]:
        cabin = self.get_cabin(cabin_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM cabins WHERE id = ?", (cabin_id,))
        return cabin

    def get_cabin(self, cabin_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM cabins WHERE id = ?", (cabin_id,)).fetchone()
            if row is None:
                raise EconomyError(f"Cabin not found: {cabin_id}")
            return self._cabin_dict(conn, row)

    def run_prestige(self, *, prime: bool = False) -> dict[str, Any]:
        with self._connect() as conn:
            spent = self._real_ap_spent(conn)
            refund_rate = Decimal("0.500") if prime else Decimal("0.300")
            refund = parse_ap(spent * refund_rate)
            shards = int(spent // Decimal("100"))
            if refund:
                self._insert_transaction(
                    conn,
                    refund,
                    "prestige_refund",
                    "Collapse refund",
                    currency=SHADOW_AP,
                    source="prestige",
                )
            if shards:
                self._insert_transaction(
                    conn,
                    Decimal(shards),
                    "prestige_shards",
                    "Singularity shard grant",
                    currency=SINGULARITY_SHARD,
                    source="prestige",
                )
            ap_balance = self._balance(conn, AP)
            if ap_balance:
                self._insert_transaction(
                    conn,
                    -ap_balance,
                    "prestige_reset",
                    "Collapse reset factual AP",
                    currency=AP,
                    source="prestige",
                    real_ap_amount=Decimal("0.000"),
                )
            max_task = conn.execute("SELECT COALESCE(MAX(id), 0) AS id FROM tasks").fetchone()
            self._set_meta(conn, "retro_buffer_cleared_task_id", str(int(max_task["id"])))
            self._set_meta(conn, "base_rate", db_ap(DEFAULT_BASE_RATE))
            self._set_meta(conn, "core_shadow_base", db_ap(DEFAULT_BASE_RATE))
            self._set_meta(conn, "cashback_level", "0")
            for key in UPGRADABLE_VECTORS:
                self._set_meta(conn, f"vector_level:{key}", "0")
            conn.execute("DELETE FROM shop_levels WHERE key NOT LIKE 'noctur.%'")
            return {
                "spent": db_ap(spent),
                "refund": db_ap(refund),
                "refund_currency": SHADOW_AP,
                "singularity_shards": shards,
            }

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
                    full_close_bonus, catalog_weight, crew_vector_bonus,
                    reward, current_reward,
                    retro_paid_base_rate, premium_received, note
                FROM tasks
                {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_task_category(self, category: str) -> dict[str, Any]:
        category = category.strip()
        if not category:
            raise ValueError("category must not be empty")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_categories (category, completed)
                VALUES (?, 0)
                ON CONFLICT(category) DO NOTHING
                """,
                (category,),
            )
            row = conn.execute(
                """
                SELECT category, completed
                FROM task_categories
                WHERE category = ?
                """,
                (category,),
            ).fetchone()
        return dict(row)

    def list_tracker_tasks(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM tracker_tasks
                ORDER BY
                    CASE status
                        WHEN 'done' THEN 0
                        WHEN 'draft' THEN 1
                        ELSE 2
                    END,
                    id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._tracker_task_dict(row) for row in rows]

    def create_tracker_task(
        self,
        *,
        title: str,
        category: str = "",
        vector: str = "code",
        units: int = 1,
        catalog_key: str | None = None,
        catalog_value: Decimal | int | str = Decimal("1"),
        priority: bool = False,
        full_close: bool = False,
        note: str = "",
    ) -> dict[str, Any]:
        title = title.strip()
        if not title:
            raise ValueError("title must not be empty")
        if units < 1:
            raise ValueError("units must be at least 1")
        vector_info = require_vector(vector)
        if catalog_key:
            find_catalog_item(catalog_key)
        catalog_value = parse_ap(catalog_value)
        category = category.strip()
        now = self._now()
        with self._connect() as conn:
            if category:
                conn.execute(
                    """
                    INSERT INTO task_categories (category, completed)
                    VALUES (?, 0)
                    ON CONFLICT(category) DO NOTHING
                    """,
                    (category,),
                )
            cur = conn.execute(
                """
                INSERT INTO tracker_tasks (
                    created_at, updated_at, category, title, status, vector, units,
                    catalog_key, catalog_value, priority, full_close, note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    now,
                    category,
                    title,
                    TRACKER_STATUS_DRAFT,
                    vector_info.key,
                    units,
                    catalog_key or "",
                    db_ap(catalog_value),
                    1 if priority else 0,
                    1 if full_close else 0,
                    note.strip(),
                ),
            )
            row = self._get_tracker_task_row(conn, int(cur.lastrowid))
        return self._tracker_task_dict(row)

    def update_tracker_task(
        self,
        task_id: int,
        *,
        title: str | None = None,
        category: str | None = None,
        vector: str | None = None,
        units: int | None = None,
        catalog_key: str | None = None,
        catalog_value: Decimal | int | str | None = None,
        priority: bool | None = None,
        full_close: bool | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            row = self._get_tracker_task_row(conn, task_id)
            if row is None:
                raise EconomyError(f"Tracker task not found: {task_id}")
            if row["status"] == TRACKER_STATUS_SUBMITTED:
                raise EconomyError("Submitted tracker tasks cannot be edited")

            values: dict[str, Any] = {}
            if title is not None:
                title = title.strip()
                if not title:
                    raise ValueError("title must not be empty")
                values["title"] = title
            if category is not None:
                category = category.strip()
                values["category"] = category
                if category:
                    conn.execute(
                        """
                        INSERT INTO task_categories (category, completed)
                        VALUES (?, 0)
                        ON CONFLICT(category) DO NOTHING
                        """,
                        (category,),
                    )
            if vector is not None:
                values["vector"] = require_vector(vector).key
            if units is not None:
                if units < 1:
                    raise ValueError("units must be at least 1")
                values["units"] = units
            if catalog_key is not None:
                if catalog_key:
                    find_catalog_item(catalog_key)
                values["catalog_key"] = catalog_key
            if catalog_value is not None:
                values["catalog_value"] = db_ap(catalog_value)
            if priority is not None:
                values["priority"] = 1 if priority else 0
            if full_close is not None:
                values["full_close"] = 1 if full_close else 0
            if note is not None:
                values["note"] = note.strip()

            if values:
                values["updated_at"] = self._now()
                assignments = ", ".join(f"{key} = ?" for key in values)
                conn.execute(
                    f"UPDATE tracker_tasks SET {assignments} WHERE id = ?",
                    (*values.values(), task_id),
                )
            row = self._get_tracker_task_row(conn, task_id)
        return self._tracker_task_dict(row)

    def mark_tracker_task_done(self, task_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = self._get_tracker_task_row(conn, task_id)
            if row is None:
                raise EconomyError(f"Tracker task not found: {task_id}")
            if row["status"] == TRACKER_STATUS_SUBMITTED:
                raise EconomyError("Submitted tracker tasks cannot be marked done")
            conn.execute(
                """
                UPDATE tracker_tasks
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (TRACKER_STATUS_DONE, self._now(), task_id),
            )
            row = self._get_tracker_task_row(conn, task_id)
        return self._tracker_task_dict(row)

    def submit_tracker_task(self, task_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = self._get_tracker_task_row(conn, task_id)
        if row is None:
            raise EconomyError(f"Tracker task not found: {task_id}")
        if row["status"] == TRACKER_STATUS_SUBMITTED:
            raise EconomyError("Tracker task is already submitted")
        if row["status"] != TRACKER_STATUS_DONE:
            raise EconomyError("Tracker task must be marked done before submit")

        result = self.complete_task(
            title=self._format_task_name(str(row["category"]), str(row["title"])),
            vector=str(row["vector"]),
            units=int(row["units"]),
            catalog_key=str(row["catalog_key"]) or None,
            catalog_value=str(row["catalog_value"]),
            priority=bool(row["priority"]),
            full_close=bool(row["full_close"]),
            note=str(row["note"]),
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tracker_tasks
                SET status = ?, updated_at = ?, economy_task_id = ?,
                    submitted_reward = ?, submitted_retro_bonus = ?
                WHERE id = ? AND status != ?
                """,
                (
                    TRACKER_STATUS_SUBMITTED,
                    self._now(),
                    result.id,
                    db_ap(result.reward),
                    db_ap(result.retro_bonus),
                    task_id,
                    TRACKER_STATUS_SUBMITTED,
                ),
            )
            row = self._get_tracker_task_row(conn, task_id)
            task_row = self._get_task_row(conn, result.id)
            crew_bonus_details = (
                self._crew_task_bonus_details(conn, str(task_row["vector"]))
                if task_row is not None
                else []
            )
        item = self._tracker_task_dict(row)
        if task_row is not None:
            item["reward_calculation"] = self._task_reward_calculation_dict(
                task_row,
                crew_bonus_details=crew_bonus_details,
            )
        return item

    def revert_tracker_task_submission(self, task_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = self._get_tracker_task_row(conn, task_id)
            if row is None:
                raise EconomyError(f"Tracker task not found: {task_id}")
            if row["status"] != TRACKER_STATUS_SUBMITTED or row["economy_task_id"] is None:
                raise EconomyError("Tracker task is not submitted")
            economy_task_id = int(row["economy_task_id"])
            reverted_reward = parse_ap(row["submitted_reward"])
            reverted_retro_bonus = parse_ap(row["submitted_retro_bonus"])
            conn.execute(
                "DELETE FROM transactions WHERE task_id = ?",
                (economy_task_id,),
            )
            conn.execute("DELETE FROM tasks WHERE id = ?", (economy_task_id,))
            conn.execute(
                """
                UPDATE tracker_tasks
                SET status = ?, updated_at = ?, economy_task_id = NULL,
                    submitted_reward = '0.000', submitted_retro_bonus = '0.000'
                WHERE id = ?
                """,
                (TRACKER_STATUS_DONE, self._now(), task_id),
            )
            updated = self._get_tracker_task_row(conn, task_id)
        item = self._tracker_task_dict(updated)
        item.update(
            {
                "reverted_economy_task_id": economy_task_id,
                "reverted_reward": db_ap(reverted_reward),
                "reverted_retro_bonus": db_ap(reverted_retro_bonus),
                "reverted_total": db_ap(
                    parse_ap(reverted_reward + reverted_retro_bonus)
                ),
            }
        )
        return item

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
            premium_rate, premium_bonus_details = self._category_premium_rate(conn)
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
                    "premium_rate": premium_rate,
                    "premium_base_rate": Decimal("0.500"),
                    "premium_bonus_rate": parse_ap(
                        premium_rate - Decimal("0.500")
                    ),
                    "premium_bonus_details": premium_bonus_details,
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
            item["premium_total"] = parse_ap(item["reward_total"] * premium_rate)
            item["premium_pending_total"] = parse_ap(
                item["premium_pending_reward_total"] * premium_rate
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
            premium_rate, premium_bonus_details = self._category_premium_rate(conn)
            category_row = conn.execute(
                "SELECT category FROM task_categories WHERE category = ?",
                (category,),
            ).fetchone()
            task_rows = conn.execute(
                "SELECT id, reward, premium_received FROM tasks WHERE category = ?",
                (category,),
            ).fetchall()
            if not task_rows and category_row is None:
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
                premium_awarded = parse_ap(pending_reward * premium_rate)
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
            result["premium_rate"] = premium_rate
            result["premium_base_rate"] = Decimal("0.500")
            result["premium_bonus_rate"] = parse_ap(
                premium_rate - Decimal("0.500")
            )
            result["premium_bonus_details"] = premium_bonus_details
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

    def _record_upgrade_only(
        self,
        conn: sqlite3.Connection,
        *,
        upgrade_type: str,
        target: str,
        level_before: str,
        level_after: str,
        cost: Decimal,
        discount: Decimal,
        note: str,
    ) -> int:
        cur = conn.execute(
            """
            INSERT INTO upgrades (
                created_at, upgrade_type, target, level_before, level_after,
                cost, cashback, note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._now(),
                upgrade_type,
                target,
                level_before,
                level_after,
                db_ap(cost),
                db_ap(discount),
                note,
            ),
        )
        return int(cur.lastrowid)

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
        extra_discount_rate: Decimal = Decimal("0.000"),
    ) -> UpgradeResult:
        cost = parse_ap(cost)
        cashback_level = self._get_int(conn, "cashback_level")
        discount = self._discount(
            conn,
            cost,
            cashback_eligible=cashback_eligible,
            extra_rate=extra_discount_rate,
        )
        final_cost = parse_ap(cost - discount)
        note = f"{target}: {level_before} -> {level_after}"
        if discount:
            percent = cashback_level * 5 + int(extra_discount_rate * 100)
            note += f" со скидкой {percent}%"
        self._ensure_can_spend(conn, final_cost)
        upgrade_id = self._record_upgrade_only(
            conn,
            upgrade_type=upgrade_type,
            target=target,
            level_before=level_before,
            level_after=level_after,
            cost=final_cost,
            discount=discount,
            note=note,
        )
        self._spend_ap_with_shadow(
            conn,
            final_cost,
            "upgrade_purchase",
            f"Покупка улучшения: {note}",
            source="terminal",
            upgrade_id=upgrade_id,
        )
        return UpgradeResult(upgrade_id, upgrade_type, target, final_cost, discount)

    def _quote_core(self, conn: sqlite3.Connection) -> UpgradeQuote:
        current_base = self._get_decimal(conn, "base_rate")
        new_base = parse_ap(current_base + self._core_step(conn))
        cost_base = current_base
        if self._get_shop_level(conn, "noctur.core_rewrite"):
            cost_base = self._core_shadow_base(conn, current_base)
        full_cost = parse_ap(cost_base * CORE_COST_MULTIPLIER)
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
        if vector_info.key not in UPGRADABLE_VECTORS:
            raise EconomyError(f"Vector {vector_info.title} is not upgradeable")
        current_level = self._get_int(conn, f"vector_level:{vector_info.key}")
        max_level = self._vector_max_level(conn)
        if current_level >= max_level:
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
        crew_effects = self._active_crew_effects(conn)
        discount = self._discount(
            conn,
            full_cost,
            cashback_eligible=True,
            extra_rate=crew_effects.vector_upgrade_discount,
        )
        return UpgradeQuote(
            target=vector_info.key,
            level_before=current_level,
            level_after=new_level,
            full_cost=full_cost,
            discount=discount,
            final_cost=parse_ap(full_cost - discount),
        )

    def _current_vector_multiplier(
        self,
        conn: sqlite3.Connection,
        vector: str,
        crew_effects: CrewEffects,
    ) -> Decimal:
        vector_info = require_vector(vector)
        if vector_info.key == "media":
            multiplier = Decimal("2.000")
        else:
            level = self._get_int(conn, f"vector_level:{vector_info.key}")
            multiplier = parse_ap(Decimal("1.000") + VECTOR_STEP * level)
        return parse_ap(
            multiplier + crew_effects.vector_bonus.get(vector_info.key, Decimal("0.000"))
        )

    def _upgrade_efficiency_entry(
        self,
        *,
        kind: str,
        target: str,
        cost: Decimal,
        impact: Decimal,
        maxed: bool,
    ) -> dict[str, Any]:
        impact = parse_ap(impact)
        cost = parse_ap(cost)
        impact_per_ap = (
            parse_ap(impact / cost)
            if cost > Decimal("0.000") and not maxed
            else Decimal("0.000")
        )
        return {
            "kind": kind,
            "target": target,
            "cost": db_ap(cost),
            "impact": db_ap(impact),
            "impact_per_ap": db_ap(impact_per_ap),
            "maxed": maxed,
        }

    def _discount(
        self,
        conn: sqlite3.Connection,
        cost: Decimal,
        *,
        cashback_eligible: bool,
        extra_rate: Decimal = Decimal("0.000"),
    ) -> Decimal:
        rate = self._discount_rate(conn, cashback_eligible=cashback_eligible)
        rate = min(rate + extra_rate, Decimal("0.95"))
        if not rate:
            return Decimal("0.000")
        return parse_ap(cost * rate)

    def _discount_rate(self, conn: sqlite3.Connection, *, cashback_eligible: bool) -> Decimal:
        cashback_level = self._get_int(conn, "cashback_level") if cashback_eligible else 0
        absolute_limit = self._get_shop_level(conn, "noctur.absolute_limit")
        rate = Decimal(cashback_level) * Decimal("0.05")
        rate += Decimal(absolute_limit) * Decimal("0.01")
        if cashback_eligible and self._get_shop_level(conn, "noctur.devaluation") >= 5:
            rate += Decimal("0.500")
        cap = min(Decimal("0.95"), Decimal("0.90") + Decimal(absolute_limit) * Decimal("0.01"))
        return min(rate, cap)

    def _core_shadow_base(self, conn: sqlite3.Connection, fallback: Decimal) -> Decimal:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = 'core_shadow_base'"
        ).fetchone()
        if row is None:
            return parse_ap(fallback)
        return parse_ap(row["value"])

    def _cashback_cost(self, current_level: int) -> Decimal:
        return parse_ap(CASHBACK_PURCHASE_BASE_COST + Decimal(current_level))

    def _core_step(self, conn: sqlite3.Connection) -> Decimal:
        level = self._get_shop_level(conn, "noctur.core_rewrite")
        if level >= 3:
            return Decimal("0.500")
        if level == 2:
            return Decimal("0.300")
        if level == 1:
            return Decimal("0.100")
        return CORE_STEP

    def _vector_max_level(self, conn: sqlite3.Connection) -> int:
        level = self._get_shop_level(conn, "noctur.limiter_removal")
        if level >= 3:
            return 60
        if level == 2:
            return 40
        if level == 1:
            return 20
        return VECTOR_MAX_LEVEL

    def _apply_cascade_resonance(self, conn: sqlite3.Connection, purchased_key: str) -> None:
        cascade_level = min(self._get_shop_level(conn, "noctur.cascade"), 2)
        if not cascade_level:
            return
        max_level = self._vector_max_level(conn)
        for key in UPGRADABLE_VECTORS:
            if key == purchased_key:
                continue
            current = self._get_int(conn, f"vector_level:{key}")
            self._set_meta(conn, f"vector_level:{key}", str(min(max_level, current + cascade_level)))

    def _estimate_upgrade_spend(self, conn: sqlite3.Connection) -> UpgradeSpendEstimate:
        state = EconomyState(
            balance=self._balance(conn),
            base_rate=self._get_decimal(conn, "base_rate"),
            cashback_level=self._get_int(conn, "cashback_level"),
            retroactive_indexing_enabled=self._get_bool(
                conn, "retroactive_indexing_enabled"
            ),
            vector_levels={
                key: self._get_int(conn, f"vector_level:{key}")
                for key in UPGRADABLE_VECTORS
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
            full_cost = parse_ap(base * CORE_COST_MULTIPLIER)
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
        discount_spent = sum(
            (self._cashback_cost(level) for level in range(state.cashback_level)),
            Decimal("0.000"),
        )
        if state.cashback_level and discount_purchase_cost != DEFAULT_DISCOUNT_PURCHASE_COST:
            discount_spent = discount_purchase_cost
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

    def _quote_retro_buffer(self, conn: sqlite3.Connection) -> RetroBufferQuote:
        current_base = self._get_decimal(conn, "base_rate")
        limit = RETRO_BUFFER_BASE_LIMIT + 10 * self._get_shop_level(conn, "noctur.quantum_archive")
        rows = self._retro_buffer_rows(conn, limit=limit)
        gross = Decimal("0.000")
        for row in rows:
            gross += self._retro_buffer_task_delta(row, current_base)
        gross = parse_ap(gross)
        tax_bypass = self._get_shop_level(conn, "noctur.tax_bypass")
        commission = max(
            Decimal("0.000"),
            RETRO_BUFFER_BASE_COMMISSION - Decimal(tax_bypass) * Decimal("0.100"),
        )
        fee = parse_ap(max(RETRO_BUFFER_MIN_FEE, gross * commission)) if gross else Decimal("0.000")
        net = parse_ap(gross - fee) if gross > fee else Decimal("0.000")
        if net and self._get_shop_level(conn, "noctur.shadow_investment"):
            net = parse_ap(net * Decimal("1.500"))
        return RetroBufferQuote(
            eligible_count=len(rows),
            limit=limit,
            gross=gross,
            fee=fee,
            net=net,
            commission_rate=commission,
            activation_allowed=gross > fee,
        )

    def _retro_buffer_payload(self, conn: sqlite3.Connection) -> dict[str, Any]:
        quote = self._quote_retro_buffer(conn)
        current_base = self._get_decimal(conn, "base_rate")
        rows = self._retro_buffer_rows(conn, limit=quote.limit)
        shadow_multiplier = (
            Decimal("1.500")
            if self._get_shop_level(conn, "noctur.shadow_investment")
            else Decimal("1.000")
        )
        tasks: list[dict[str, Any]] = []
        for row in rows:
            gross_delta = self._retro_buffer_task_delta(row, current_base)
            fee_share = Decimal("0.000")
            net_delta = Decimal("0.000")
            if gross_delta and quote.gross:
                fee_share = parse_ap(quote.fee * gross_delta / quote.gross)
                net_delta = (
                    parse_ap(gross_delta - fee_share)
                    if gross_delta > fee_share
                    else Decimal("0.000")
                )
                if net_delta and shadow_multiplier != Decimal("1.000"):
                    net_delta = parse_ap(net_delta * shadow_multiplier)
            tasks.append(
                {
                    "id": int(row["id"]),
                    "created_at": row["created_at"],
                    "category": row["category"],
                    "title": row["title"],
                    "units": int(row["units"]),
                    "vector": row["vector"],
                    "paid_base_rate": db_ap(row["retro_paid_base_rate"]),
                    "current_base_rate": db_ap(current_base),
                    "current_reward": db_ap(row["current_reward"]),
                    "gross_delta": db_ap(gross_delta),
                    "fee_share": db_ap(fee_share),
                    "net_delta": db_ap(net_delta),
                    "eligible": gross_delta > 0,
                }
            )
        return {
            "eligible_count": quote.eligible_count,
            "limit": quote.limit,
            "gross": db_ap(quote.gross),
            "fee": db_ap(quote.fee),
            "net": db_ap(quote.net),
            "commission_rate": db_ap(quote.commission_rate),
            "activation_allowed": quote.activation_allowed,
            "tasks": tasks,
        }

    def _retro_buffer_rows(
        self,
        conn: sqlite3.Connection,
        *,
        limit: int,
    ) -> list[sqlite3.Row]:
        cleared_id = self._get_int(conn, "retro_buffer_cleared_task_id")
        return conn.execute(
            """
            SELECT id, created_at, category, title, vector, units,
                vector_multiplier, priority_multiplier, full_close_bonus,
                catalog_weight, crew_vector_bonus, current_reward,
                retro_paid_base_rate
            FROM tasks
            WHERE id > ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (cleared_id, limit),
        ).fetchall()

    def _retro_buffer_task_delta(
        self,
        row: sqlite3.Row,
        current_base: Decimal,
    ) -> Decimal:
        paid_base = parse_ap(row["retro_paid_base_rate"])
        if paid_base >= current_base:
            return Decimal("0.000")
        return parse_ap(
            Decimal(row["units"])
            * (current_base - paid_base)
            * self._task_multiplier(row)
            * parse_ap(row["catalog_weight"])
        )

    def _activate_retro_buffer(self, conn: sqlite3.Connection) -> UpgradeResult:
        quote = self._quote_retro_buffer(conn)
        if not quote.activation_allowed:
            raise EconomyError(
                f"Retro buffer is not profitable: gross {db_ap(quote.gross)}, fee {db_ap(quote.fee)}"
            )
        current_base = self._get_decimal(conn, "base_rate")
        rows = self._retro_buffer_rows(conn, limit=quote.limit)
        max_task_id = 0
        for row in rows:
            max_task_id = max(max_task_id, int(row["id"]))
            delta = self._retro_buffer_task_delta(row, current_base)
            if not delta:
                continue
            conn.execute(
                """
                UPDATE tasks
                SET retro_paid_base_rate = ?, current_reward = ?
                WHERE id = ?
                """,
                (
                    db_ap(current_base),
                    db_ap(parse_ap(parse_ap(row["current_reward"]) + delta)),
                    row["id"],
                ),
            )
        self._set_meta(conn, "retro_buffer_cleared_task_id", str(max_task_id))
        upgrade_id = self._record_upgrade_only(
            conn,
            upgrade_type="retro_buffer",
            target="Retro buffer",
            level_before="0",
            level_after=str(max_task_id),
            cost=Decimal("0.000"),
            discount=quote.fee,
            note=f"Retro buffer gross {db_ap(quote.gross)}, fee {db_ap(quote.fee)}",
        )
        self._insert_transaction(
            conn,
            quote.net,
            "retro_bonus",
            f"Retro buffer net reward: {quote.eligible_count} tasks",
            upgrade_id=upgrade_id,
            source="retro_buffer",
        )
        return UpgradeResult(upgrade_id, "retro_buffer", "Retro buffer", quote.net, quote.fee)

    def _apply_retroactive_indexing(
        self, conn: sqlite3.Connection, *, exclude_task_id: int
    ) -> list[RetroBonusDetail]:
        if not self._get_bool(conn, "retroactive_indexing_enabled"):
            return []
        current_base = self._get_decimal(conn, "base_rate")
        rows = conn.execute(
            """
            SELECT id, category, title, units, vector_multiplier, priority_multiplier,
                full_close_bonus, catalog_weight, crew_vector_bonus, current_reward,
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
                * self._task_multiplier(row)
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
        currency: str = AP,
        source: str = "",
        purchase_id: int | None = None,
        real_ap_amount: Decimal | None = None,
        shadow_ap_amount: Decimal | None = None,
    ) -> int:
        cur = conn.execute(
            """
            INSERT INTO transactions (
                created_at, amount, kind, note, task_id, upgrade_id,
                currency, source, purchase_id, real_ap_amount, shadow_ap_amount
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._now(),
                db_ap(amount),
                kind,
                note,
                task_id,
                upgrade_id,
                currency,
                source,
                purchase_id,
                db_ap(real_ap_amount if real_ap_amount is not None else (amount if currency == AP else 0)),
                db_ap(shadow_ap_amount if shadow_ap_amount is not None else (amount if currency == SHADOW_AP else 0)),
            ),
        )
        return int(cur.lastrowid)

    def _spend_currency(
        self,
        conn: sqlite3.Connection,
        amount: Decimal,
        currency: str,
        note: str,
        *,
        purchase_id: int | None = None,
    ) -> None:
        amount = parse_ap(amount)
        if not amount:
            return
        if currency == AP:
            self._spend_ap_with_shadow(
                conn,
                amount,
                "shop_purchase",
                note,
                source="shop",
                purchase_id=purchase_id,
            )
            return
        if self._balance(conn, currency) < amount:
            raise InsufficientBalanceError(
                f"Not enough {currency}: need {db_ap(amount)}, balance {db_ap(self._balance(conn, currency))}"
            )
        self._insert_transaction(
            conn,
            -amount,
            "shop_purchase",
            note,
            currency=currency,
            source="shop",
            purchase_id=purchase_id,
        )

    def _spend_ap_with_shadow(
        self,
        conn: sqlite3.Connection,
        amount: Decimal,
        kind: str,
        note: str,
        *,
        source: str,
        upgrade_id: int | None = None,
        purchase_id: int | None = None,
    ) -> tuple[Decimal, Decimal]:
        amount = parse_ap(amount)
        real_balance = self._balance(conn, AP)
        shadow_balance = self._balance(conn, SHADOW_AP)
        real_spend = min(max(real_balance, Decimal("0.000")), amount)
        shadow_spend = parse_ap(amount - real_spend)
        if shadow_spend and shadow_balance < shadow_spend:
            total = parse_ap(real_balance + shadow_balance)
            raise InsufficientBalanceError(
                f"Not enough AP: need {db_ap(amount)}, balance {db_ap(total)}"
            )
        if real_spend:
            self._insert_transaction(
                conn,
                -real_spend,
                kind,
                note,
                currency=AP,
                source=source,
                upgrade_id=upgrade_id,
                purchase_id=purchase_id,
                real_ap_amount=-real_spend,
            )
        if shadow_spend:
            self._insert_transaction(
                conn,
                -shadow_spend,
                kind,
                note,
                currency=SHADOW_AP,
                source=source,
                upgrade_id=upgrade_id,
                purchase_id=purchase_id,
                shadow_ap_amount=-shadow_spend,
            )
        return real_spend, shadow_spend

    def _shop_quote_payload(self, quote: ShopQuote) -> dict[str, Any]:
        return {
            "item_key": quote.item_key,
            "title": quote.title,
            "section": quote.section,
            "target": quote.target,
            "quantity": quote.quantity,
            "currency": quote.currency,
            "full_cost": db_ap(quote.full_cost),
            "discount": db_ap(quote.discount),
            "final_cost": db_ap(quote.final_cost),
            "available": quote.available,
            "reason": quote.reason,
            "metadata": quote.metadata or {},
        }

    def _quote_shop_purchase(
        self,
        conn: sqlite3.Connection,
        item_key: str,
        *,
        target: str,
        quantity: int,
        options: dict[str, Any] | None,
    ) -> ShopQuote:
        if quantity < 1:
            raise ValueError("quantity must be at least 1")
        item = require_shop_item(item_key)
        options = options or {}
        full_cost = item.base_cost * quantity
        metadata: dict[str, Any] = {}
        available = True
        reason = ""
        currency = item.currency

        if item.cost_formula == "core":
            quote = self._quote_core(conn)
            full_cost = quote.full_cost
            discount = quote.discount
            final_cost = quote.final_cost
            metadata = {
                "level_before": db_ap(quote.level_before),
                "level_after": db_ap(quote.level_after),
            }
            return ShopQuote(item.key, item.title, item.section, target, 1, currency, full_cost, discount, final_cost, available, reason, metadata)
        if item.cost_formula == "vector":
            if not target:
                available = False
                reason = "target vector is required"
                full_cost = Decimal("0.000")
                discount = Decimal("0.000")
                final_cost = Decimal("0.000")
            else:
                quote = self._quote_vector(conn, target)
                full_cost = quote.full_cost
                discount = quote.discount
                final_cost = quote.final_cost
                available = not quote.maxed
                reason = "vector is maxed" if quote.maxed else ""
                metadata = {"level_before": quote.level_before, "level_after": quote.level_after}
            return ShopQuote(item.key, item.title, item.section, target, 1, currency, full_cost, discount, final_cost, available, reason, metadata)
        if item.cost_formula == "cashback":
            current = self._get_int(conn, "cashback_level")
            available = current < CASHBACK_MAX_LEVEL
            reason = "cashback is maxed" if not available else ""
            full_cost = self._cashback_cost(current)
            metadata = {"level_before": current, "level_after": min(CASHBACK_MAX_LEVEL, current + 1)}
        elif item.key == "genesis.rank_change":
            available = False
            reason = "use the crew promotion action"
            full_cost = Decimal("0.000")
        elif item.cost_formula == "retro_buffer":
            retro = self._quote_retro_buffer(conn)
            full_cost = retro.fee
            metadata = {
                "eligible_count": retro.eligible_count,
                "limit": retro.limit,
                "gross": db_ap(retro.gross),
                "fee": db_ap(retro.fee),
                "net": db_ap(retro.net),
                "commission_rate": db_ap(retro.commission_rate),
            }
            available = retro.activation_allowed
            reason = "" if available else "gross reward must be greater than fee"
        elif item.cost_formula in {"linear_level", "shard_linear", "cascade"}:
            current = self._get_shop_level(conn, self._shop_level_key(item.key, target))
            if item.max_level is not None and current >= item.max_level:
                available = False
                reason = "max level reached"
            if item.cost_formula == "shard_linear":
                full_cost = sum(
                    parse_ap(item.base_cost + Decimal(current + offset))
                    for offset in range(quantity)
                )
            elif item.cost_formula == "cascade":
                full_cost = sum(
                    parse_ap(item.base_cost + Decimal(3 * (current + offset)))
                    for offset in range(quantity)
                )
            else:
                full_cost = item.base_cost * quantity
            metadata = {"level_before": current, "level_after": current + quantity}
        elif item.cost_formula == "attribute":
            if not target:
                available = False
                reason = "attribute target is required"
            current = self._get_shop_level(conn, self._shop_level_key(item.key, target))
            full_cost = sum(self._attribute_level_cost(current + offset + 1) for offset in range(quantity))
            metadata = {"level_before": current, "level_after": current + quantity}
        elif item.cost_formula == "prime":
            loyalty = self._get_int(conn, "prime_loyalty_weeks")
            full_cost = parse_ap(max(Decimal("10.000"), Decimal("20.000") - Decimal(loyalty)) * quantity)
        elif item.cost_formula == "rollback_posts":
            posts = int(options.get("posts", quantity))
            if posts <= 4:
                full_cost = Decimal("0.200") * posts
            elif posts <= 9:
                full_cost = Decimal("2.000") * posts
            else:
                full_cost = Decimal("4.000") * posts
            metadata = {"posts": posts}
        elif item.cost_formula == "rental":
            prerequisites = int(options.get("prerequisites", 0))
            full_cost = parse_ap(Decimal("1.000") + Decimal("0.500") * prerequisites)
        elif item.cost_formula == "infiltrator":
            full_cost = Decimal("0.500")
            for key, extra in {"crowd": "0.500", "named": "0.500", "target": "1.000", "boss": "2.500"}.items():
                if options.get(key):
                    full_cost += Decimal(extra)
        elif item.cost_formula == "skip":
            full_cost = parse_ap(Decimal(str(options.get("cost_per_obstacle", 1))) * int(options.get("obstacles", quantity)))
        elif item.cost_formula == "perfect_algorithm":
            full_cost = parse_ap(Decimal(str(options.get("setting_cost", 0))) * Decimal("4.000"))

        full_cost = parse_ap(full_cost)
        crew_effects = self._active_crew_effects(conn)
        if item.key in crew_effects.free_shop_items:
            discount = full_cost
            final_cost = Decimal("0.000")
            return ShopQuote(item.key, item.title, item.section, target, quantity, currency, full_cost, discount, final_cost, available, reason, metadata)
        discount = self._shop_discount(conn, item.key, target, full_cost, currency, crew_effects=crew_effects)
        final_cost = parse_ap(full_cost - discount)
        if currency == AP and full_cost and final_cost < Decimal("0.100"):
            final_cost = Decimal("0.100")
            discount = parse_ap(full_cost - final_cost)
        return ShopQuote(item.key, item.title, item.section, target, quantity, currency, full_cost, discount, final_cost, available, reason, metadata)

    def _shop_discount(
        self,
        conn: sqlite3.Connection,
        item_key: str,
        target: str,
        full_cost: Decimal,
        currency: str,
        crew_effects: CrewEffects | None = None,
    ) -> Decimal:
        if currency != AP or not full_cost:
            return Decimal("0.000")
        crew_effects = crew_effects or self._active_crew_effects(conn)
        target_key = target or item_key
        target_discount = Decimal(self._get_target_discount(conn, target_key)) * Decimal("0.05")
        item = require_shop_item(item_key)
        rate = target_discount + self._global_shop_discount_rate(conn, item)
        rate += crew_effects.shop_percent_discount.get(item.key, Decimal("0.000"))
        rate += Decimal(self._get_shop_level(conn, "noctur.absolute_limit")) * Decimal("0.01")
        cap = Decimal("0.95") if self._get_shop_level(conn, "noctur.absolute_limit") else Decimal("0.90")
        percent_discount = parse_ap(full_cost * min(rate, cap))
        crew_discount = crew_effects.shop_flat_discount.get(item.key, Decimal("0.000"))
        return parse_ap(min(full_cost, percent_discount + crew_discount))

    def _global_shop_discount_rate(self, conn: sqlite3.Connection, item: Any) -> Decimal:
        level = min(self._get_shop_level(conn, "noctur.devaluation"), 5)
        if not level:
            return Decimal("0.000")
        if item.key in {"terminal.core", "terminal.vector"}:
            return Decimal("0.500") if level >= 5 else Decimal("0.000")
        route_items = {
            "expedition.target_request",
            "expedition.ng",
            "expedition.ng_plus",
            "expedition.hijack",
            "expedition.isolated_singularity",
        }
        if item.section in {"world", "recreation"} or item.key in route_items or item.key == "prime.subscription":
            return Decimal(level) * Decimal("0.100")
        return Decimal("0.000")

    def _active_crew_effects(self, conn: sqlite3.Connection) -> CrewEffects:
        rows = conn.execute(
            """
            SELECT rank, sedative_dose, tags, full_tags, dominants
            FROM cabins
            WHERE active = 1
            """
        ).fetchall()
        base_rate_bonus = Decimal("0.000")
        vector_bonus: dict[str, Decimal] = {}
        full_close_bonus = Decimal("0.000")
        shop_flat_discount: dict[str, Decimal] = {}
        shop_percent_discount: dict[str, Decimal] = {}
        vector_upgrade_discount = Decimal("0.000")
        free_shop_items: set[str] = set()
        tag_counts: dict[str, int] = {}

        for row in rows:
            for tag in self._crew_tags(row["tags"], row["full_tags"]):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            coefficient = self._crew_efficiency(row["rank"], row["sedative_dose"])
            for dominant in self._dominant_traits(row["dominants"]):
                name = self._normalize_trait_name(dominant["name"])
                level = dominant["level"]
                if name in {"суб-администратор", "аналитика / кодинг", "аналитика", "кодинг"}:
                    bonus = self._scaled_percent(self._level_value(level, ("0.10", "0.15", "0.20", "0.30", "0.40")), coefficient)
                    vector_bonus["code"] = parse_ap(vector_bonus.get("code", Decimal("0.000")) + bonus)
                elif name in {"скорость вспышки", "вдохновительница"}:
                    base_rate_bonus += self._scaled_percent(self._level_value(level, ("0.02", "0.04", "0.06", "0.08", "0.10")), coefficient)
                elif name == "чтение ауры":
                    full_close_bonus += self._scaled_percent(Decimal("0.03") * level, coefficient)
                elif name == "мимикрия днк":
                    discount = self._scaled_flat_ap(Decimal("0.5") * level, coefficient)
                    shop_flat_discount["world.infiltrator"] = max(
                        shop_flat_discount.get("world.infiltrator", Decimal("0.000")),
                        discount,
                    )
                elif name == "эмпатия":
                    discount = self._scaled_flat_ap(self._level_value(level, ("0.1", "0.2", "0.3")), coefficient)
                    shop_flat_discount["world.infiltrator"] = max(
                        shop_flat_discount.get("world.infiltrator", Decimal("0.000")),
                        discount,
                    )
                elif name == "блокировка точек":
                    discount = self._scaled_flat_ap(Decimal("0.2") * level, coefficient)
                    shop_flat_discount["world.skip"] = max(
                        shop_flat_discount.get("world.skip", Decimal("0.000")),
                        discount,
                    )

        if self._tag_count(tag_counts, "киберпространство", "sci-fi", "scifi") >= 3:
            vector_upgrade_discount = max(vector_upgrade_discount, Decimal("0.150"))
        if self._tag_count(tag_counts, "фэнтези", "фентези", "магия") >= 3:
            for item_key in ("world.adaptation", "world.prerequisite"):
                shop_percent_discount[item_key] = max(
                    shop_percent_discount.get(item_key, Decimal("0.000")),
                    Decimal("0.300"),
                )
        if self._tag_count(tag_counts, "эрудит", "эрудиция") >= 3:
            free_shop_items.add("expedition.intel")
        if self._tag_count(tag_counts, "нестабильность") >= 3:
            full_close_bonus += Decimal("0.300")
        if self._tag_count(tag_counts, "постапокалипсис", "выживание") >= 3:
            shop_percent_discount["hub.extractor"] = max(
                shop_percent_discount.get("hub.extractor", Decimal("0.000")),
                Decimal("0.500"),
            )

        return CrewEffects(
            base_rate_bonus=parse_ap(base_rate_bonus),
            vector_bonus={key: parse_ap(value) for key, value in vector_bonus.items()},
            full_close_bonus=parse_ap(full_close_bonus),
            shop_flat_discount={key: parse_ap(value) for key, value in shop_flat_discount.items()},
            shop_percent_discount={
                key: parse_ap(value) for key, value in shop_percent_discount.items()
            },
            vector_upgrade_discount=parse_ap(vector_upgrade_discount),
            free_shop_items=frozenset(free_shop_items),
        )

    def _category_premium_rate(
        self,
        conn: sqlite3.Connection,
    ) -> tuple[Decimal, list[dict[str, str]]]:
        rows = conn.execute(
            """
            SELECT name, dominants
            FROM cabins
            WHERE active = 1
            """
        ).fetchall()
        bonus = Decimal("0.000")
        details: list[dict[str, str]] = []
        for row in rows:
            for dominant in self._dominant_traits(row["dominants"]):
                if self._normalize_trait_name(dominant["name"]) != "чтение ауры":
                    continue
                amount = Decimal("0.050")
                bonus = parse_ap(bonus + amount)
                details.append(
                    {
                        "source": str(row["name"]),
                        "trait": dominant["name"],
                        "amount": db_ap(amount),
                    }
                )
        return parse_ap(Decimal("0.500") + bonus), details

    def _crew_task_bonus_details(
        self,
        conn: sqlite3.Connection,
        vector: str,
    ) -> list[dict[str, str]]:
        rows = conn.execute(
            """
            SELECT name, rank, sedative_dose, dominants
            FROM cabins
            WHERE active = 1
            """
        ).fetchall()
        details: list[dict[str, str]] = []
        for row in rows:
            cap = self._crew_efficiency_cap(row["rank"])
            sedative = parse_ap(row["sedative_dose"])
            coefficient = self._crew_efficiency(row["rank"], sedative)
            cap_percent = parse_ap(cap * Decimal("100"))
            for dominant in self._dominant_traits(row["dominants"]):
                name = self._normalize_trait_name(dominant["name"])
                level = dominant["level"]
                kind = ""
                label = ""
                nominal = Decimal("0.000")
                if name in {"суб-администратор", "аналитика / кодинг", "аналитика", "кодинг"}:
                    if vector != "code":
                        continue
                    kind = "vector"
                    label = "Вектор"
                    nominal = self._level_value(level, ("0.10", "0.15", "0.20", "0.30", "0.40"))
                elif name in {"скорость вспышки", "вдохновительница"}:
                    kind = "base_rate"
                    label = "База"
                    nominal = self._level_value(level, ("0.02", "0.04", "0.06", "0.08", "0.10"))
                elif name == "чтение ауры":
                    kind = "full_close"
                    label = "Полное закрытие"
                    nominal = Decimal("0.03") * level
                if not kind:
                    continue
                amount = self._scaled_percent(nominal, coefficient)
                details.append(
                    {
                        "kind": kind,
                        "label": label,
                        "source": str(row["name"]),
                        "trait": dominant["name"],
                        "nominal": db_ap(nominal),
                        "cap_percent": db_ap(cap_percent),
                        "sedative_percent": db_ap(sedative),
                        "amount": db_ap(amount),
                        "formula": (
                            f"{format_ap(nominal)} * "
                            f"({format_ap(cap_percent)} - {format_ap(sedative)} СД)%"
                            f" = {format_ap(amount)}"
                        ),
                    }
                )
        return details

    def _crew_tags(self, tags: str | None, full_tags: str | None) -> list[str]:
        raw = tags or full_tags or ""
        bracketed = re.findall(r"\[([^\]]+)\]", raw)
        parts = bracketed or re.split(r"[,;/]", raw)
        normalized: list[str] = []
        for part in parts:
            tag = self._normalize_trait_name(str(part))
            if tag:
                normalized.append(tag)
        return normalized

    def _tag_count(self, counts: dict[str, int], *tags: str) -> int:
        return sum(counts.get(self._normalize_trait_name(tag), 0) for tag in tags)

    def _dominant_traits(self, raw: str) -> list[dict[str, Any]]:
        try:
            parsed = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        traits = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            try:
                level = int(item.get("level", 1))
            except (TypeError, ValueError):
                level = 1
            traits.append({"name": name, "level": max(1, level)})
        return traits

    def _crew_efficiency(self, rank: str, sedative_dose: Decimal | int | str) -> Decimal:
        cap = self._crew_efficiency_cap(rank)
        sedative = parse_ap(sedative_dose) / Decimal("100")
        return max(Decimal("0.000"), parse_ap(cap - sedative))

    def _crew_efficiency_cap(self, rank: str) -> Decimal:
        normalized_rank = str(rank).strip().lower().replace("[", "").replace("]", "")
        if normalized_rank.startswith("sr"):
            return Decimal("1.500")
        elif normalized_rank.startswith("s"):
            return Decimal("1.300")
        elif normalized_rank.startswith("a"):
            return Decimal("1.100")
        elif normalized_rank.startswith("e"):
            return Decimal("0.800")
        return Decimal("1.000")

    def _normalize_trait_name(self, name: str) -> str:
        return (
            name.strip()
            .lower()
            .replace("ё", "е")
            .removeprefix("[")
            .removesuffix("]")
            .strip()
        )

    def _level_value(self, level: int, values: tuple[str, ...]) -> Decimal:
        index = min(max(level, 1), len(values)) - 1
        return Decimal(values[index])

    def _scaled_percent(self, value: Decimal, coefficient: Decimal) -> Decimal:
        return parse_ap(value * coefficient)

    def _scaled_flat_ap(self, value: Decimal, coefficient: Decimal) -> Decimal:
        scaled = value * coefficient
        return scaled.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    def _insert_shop_purchase(self, conn: sqlite3.Connection, quote: ShopQuote, note: str) -> int:
        cur = conn.execute(
            """
            INSERT INTO shop_purchases (
                created_at, item_key, title, section, target, quantity, currency,
                full_cost, discount, final_cost, note, effect_kind, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._now(),
                quote.item_key,
                quote.title,
                quote.section,
                quote.target,
                quote.quantity,
                quote.currency,
                db_ap(quote.full_cost),
                db_ap(quote.discount),
                db_ap(quote.final_cost),
                note.strip(),
                require_shop_item(quote.item_key).effect_kind,
                str(quote.metadata or {}),
            ),
        )
        return int(cur.lastrowid)

    def _apply_shop_effect(
        self,
        conn: sqlite3.Connection,
        quote: ShopQuote,
        purchase_id: int,
        note: str,
    ) -> None:
        item = require_shop_item(quote.item_key)
        key = self._shop_level_key(item.key, quote.target)
        if item.effect_kind in {
            "level",
            "hub_upgrade",
            "target_discount",
            "noctur_upgrade",
            "overclock_unlock",
            "retro_upgrade",
            "expedition_upgrade",
            "world_effect",
            "genesis_upgrade",
        }:
            self._set_shop_level(conn, key, self._get_shop_level(conn, key) + quote.quantity)
        if item.effect_kind == "target_discount" and quote.target:
            conn.execute(
                """
                INSERT INTO target_discounts (target, level)
                VALUES (?, 1)
                ON CONFLICT(target) DO UPDATE SET level = level + 1
                """,
                (quote.target,),
            )
        if item.effect_kind == "prime_extend":
            self._set_meta(conn, "prime_active", "1")
            if not self._get_meta(conn, "prime_active_since"):
                self._set_meta(conn, "prime_active_since", self._now()[:10])
            self._set_meta(
                conn,
                "prime_weeks_purchased",
                str(self._get_int(conn, "prime_weeks_purchased") + quote.quantity),
            )
            self._set_meta(
                conn,
                "prime_loyalty_weeks",
                str(self._get_int(conn, "prime_loyalty_weeks") + quote.quantity),
            )
        if item.effect_kind == "gacha_roll":
            rolls = self._get_int(conn, "neural_shard_pity") + quote.quantity
            shards, pity = divmod(rolls, 100)
            self._set_meta(conn, "neural_shard_pity", str(pity))
            if shards:
                self._insert_transaction(
                    conn,
                    Decimal(shards),
                    "neural_shard_pity",
                    "Neural shard pity grant",
                    currency=NEURAL_SHARD,
                    source="genesis",
                    purchase_id=purchase_id,
                )
        conn.execute(
            """
            INSERT OR REPLACE INTO active_effects (key, title, value, expires_at, note)
            VALUES (?, ?, ?, '', ?)
            """,
            (key, item.title, str(self._get_shop_level(conn, key)), note.strip()),
        )

    def _record_terminal_purchase(
        self,
        item_key: str,
        result: UpgradeResult,
        quantity: int,
        note: str,
    ) -> dict[str, Any]:
        item = require_shop_item(item_key)
        quote = ShopQuote(
            item.key,
            item.title,
            item.section,
            result.target,
            quantity,
            AP,
            parse_ap(result.cost + result.cashback),
            result.cashback,
            result.cost,
            True,
            "",
            {"upgrade_id": result.id},
        )
        with self._connect() as conn:
            purchase_id = self._insert_shop_purchase(conn, quote, note)
            conn.execute(
                "UPDATE transactions SET purchase_id = ? WHERE upgrade_id = ?",
                (purchase_id, result.id),
            )
        return self.get_shop_purchase(purchase_id)

    def _shop_purchase_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        return item

    def _history_purchase_entry(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": f"purchase:{row['id']}",
            "kind": "purchase",
            "created_at": row["created_at"],
            "title": row["title"],
            "section": row["section"],
            "amount": db_ap(row["final_cost"]),
            "currency": row["currency"],
            "target": row["target"],
            "note": row["note"],
            "purchase_id": int(row["id"]),
            "tracker_task_id": None,
            "economy_task_id": None,
            "revertible": False,
        }

    def _history_task_submission_entry(self, row: sqlite3.Row) -> dict[str, Any]:
        reward = parse_ap(row["submitted_reward"])
        retro_bonus = parse_ap(row["submitted_retro_bonus"])
        amount = parse_ap(reward + retro_bonus)
        return {
            "id": f"task_submit:{row['id']}",
            "kind": "task_submit",
            "created_at": row["updated_at"],
            "title": self._format_task_name(str(row["category"]), str(row["title"])),
            "section": "task",
            "amount": db_ap(amount),
            "currency": AP,
            "target": f"task:{row['id']}",
            "note": "",
            "purchase_id": None,
            "tracker_task_id": int(row["id"]),
            "economy_task_id": int(row["economy_task_id"]),
            "revertible": True,
        }

    def _shop_level_key(self, item_key: str, target: str = "") -> str:
        return f"{item_key}:{target.strip()}" if target.strip() else item_key

    def _get_shop_level(self, conn: sqlite3.Connection, key: str) -> int:
        row = conn.execute("SELECT level FROM shop_levels WHERE key = ?", (key,)).fetchone()
        return int(row["level"]) if row is not None else 0

    def _set_shop_level(self, conn: sqlite3.Connection, key: str, level: int) -> None:
        conn.execute(
            """
            INSERT INTO shop_levels (key, level)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET level = excluded.level
            """,
            (key, level),
        )

    def _get_target_discount(self, conn: sqlite3.Connection, target: str) -> int:
        row = conn.execute(
            "SELECT level FROM target_discounts WHERE target = ?",
            (target,),
        ).fetchone()
        return int(row["level"]) if row is not None else 0

    def _attribute_level_cost(self, next_level: int) -> Decimal:
        if next_level <= 100:
            tier = (next_level - 1) // 10
            return parse_ap(Decimal("0.100") * (Decimal(2) ** tier))
        return Decimal("2.000")

    def _real_ap_spent(self, conn: sqlite3.Connection) -> Decimal:
        rows = conn.execute(
            "SELECT amount, currency FROM transactions WHERE amount < 0"
        ).fetchall()
        total = Decimal("0.000")
        for row in rows:
            if str(row["currency"]) == AP:
                total += abs(parse_ap(row["amount"]))
        return parse_ap(total)

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

    def _get_task_row(
        self, conn: sqlite3.Connection, task_id: int
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT id, created_at, category, title, vector, units, base_rate,
                vector_level, vector_multiplier, priority_multiplier,
                full_close_bonus, catalog_weight, crew_vector_bonus,
                reward, current_reward, retro_paid_base_rate, premium_received, note
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()

    def _get_tracker_task_row(
        self, conn: sqlite3.Connection, task_id: int
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT *
            FROM tracker_tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()

    def _tracker_task_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["priority"] = bool(item["priority"])
        item["full_close"] = bool(item["full_close"])
        item["submitted"] = item["status"] == TRACKER_STATUS_SUBMITTED
        return item

    def _task_multiplier(self, row: sqlite3.Row | dict[str, Any]) -> Decimal:
        return parse_ap(
            parse_ap(row["vector_multiplier"])
            * parse_ap(row["priority_multiplier"])
            * parse_ap(row["full_close_bonus"])
        )

    def _task_reward_calculation_dict(
        self,
        row: sqlite3.Row | dict[str, Any],
        *,
        crew_bonus_details: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        units = int(row["units"])
        base_rate = parse_ap(row["base_rate"])
        core_base_rate = parse_ap(row["retro_paid_base_rate"])
        crew_base_bonus = parse_ap(base_rate - core_base_rate)
        catalog_weight = parse_ap(row["catalog_weight"])
        vector_multiplier = parse_ap(row["vector_multiplier"])
        crew_vector_bonus = parse_ap(row["crew_vector_bonus"])
        purchased_vector_multiplier = parse_ap(vector_multiplier - crew_vector_bonus)
        priority_multiplier = parse_ap(row["priority_multiplier"])
        full_close_bonus = parse_ap(row["full_close_bonus"])
        base_catalog_total = parse_ap(Decimal(units) * base_rate * catalog_weight)
        base_vector_total = parse_ap(
            base_catalog_total * vector_multiplier * priority_multiplier
        )
        full_close_premium = (
            parse_ap(base_vector_total * (full_close_bonus - Decimal("1.000")))
            if full_close_bonus > Decimal("1.000")
            else Decimal("0.000")
        )
        reward = parse_ap(row["reward"])
        return {
            "units": units,
            "base_rate": db_ap(base_rate),
            "core_base_rate": db_ap(core_base_rate),
            "crew_base_bonus": db_ap(crew_base_bonus),
            "catalog_weight": db_ap(catalog_weight),
            "purchased_vector_multiplier": db_ap(purchased_vector_multiplier),
            "vector_multiplier": db_ap(vector_multiplier),
            "priority_multiplier": db_ap(priority_multiplier),
            "full_close_bonus": db_ap(full_close_bonus),
            "crew_vector_bonus": db_ap(crew_vector_bonus),
            "base_catalog_total": db_ap(base_catalog_total),
            "base_vector_total": db_ap(base_vector_total),
            "full_close_premium": db_ap(full_close_premium),
            "reward": db_ap(reward),
            "crew_bonus_details": crew_bonus_details or [],
            "formula": (
                f"{units} * ({db_ap(core_base_rate)} + {db_ap(crew_base_bonus)})"
                f" * {db_ap(catalog_weight)} * "
                f"({db_ap(purchased_vector_multiplier)} + {db_ap(crew_vector_bonus)})"
                f" * {db_ap(priority_multiplier)}"
            ),
        }

    def _format_reward_formula(self, parts: dict[Decimal, int], total: Decimal) -> str:
        terms = []
        for reward, count in sorted(parts.items()):
            if count == 1:
                terms.append(db_ap(reward).rstrip("0").rstrip("."))
            else:
                terms.append(f"{db_ap(reward).rstrip('0').rstrip('.')}x{count}")
        return f"{' + '.join(terms)} = {db_ap(total).rstrip('0').rstrip('.')}"

    def _ensure_can_spend(self, conn: sqlite3.Connection, amount: Decimal) -> None:
        balance = parse_ap(self._balance(conn, AP) + self._balance(conn, SHADOW_AP))
        if parse_ap(balance - amount) < 0:
            raise InsufficientBalanceError(
                f"Not enough AP: need {db_ap(amount)}, balance {db_ap(balance)}"
            )

    def _balance(self, conn: sqlite3.Connection, currency: str = AP) -> Decimal:
        rows = conn.execute(
            "SELECT amount FROM transactions WHERE currency = ?",
            (currency,),
        ).fetchall()
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
                upgrade_id INTEGER,
                currency TEXT NOT NULL DEFAULT 'ap',
                source TEXT NOT NULL DEFAULT '',
                purchase_id INTEGER,
                real_ap_amount TEXT NOT NULL DEFAULT '0.000',
                shadow_ap_amount TEXT NOT NULL DEFAULT '0.000'
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
                crew_vector_bonus TEXT NOT NULL DEFAULT '0.000',
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

            CREATE TABLE IF NOT EXISTS tracker_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                vector TEXT NOT NULL,
                units INTEGER NOT NULL,
                catalog_key TEXT NOT NULL DEFAULT '',
                catalog_value TEXT NOT NULL DEFAULT '1.000',
                priority INTEGER NOT NULL DEFAULT 0,
                full_close INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                economy_task_id INTEGER,
                submitted_reward TEXT NOT NULL DEFAULT '0.000',
                submitted_retro_bonus TEXT NOT NULL DEFAULT '0.000',
                CHECK(status IN ('draft', 'done', 'submitted'))
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

            CREATE TABLE IF NOT EXISTS shop_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                item_key TEXT NOT NULL,
                title TEXT NOT NULL,
                section TEXT NOT NULL,
                target TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 1,
                currency TEXT NOT NULL DEFAULT 'ap',
                full_cost TEXT NOT NULL,
                discount TEXT NOT NULL,
                final_cost TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                effect_kind TEXT NOT NULL DEFAULT 'manual',
                metadata TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS shop_levels (
                key TEXT PRIMARY KEY,
                level INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS target_discounts (
                target TEXT PRIMARY KEY,
                level INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS active_effects (
                key TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                value TEXT NOT NULL,
                expires_at TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS expeditions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'planned',
                difficulty TEXT NOT NULL DEFAULT 'normal',
                note TEXT NOT NULL DEFAULT '',
                cached_until TEXT NOT NULL DEFAULT '',
                rotten INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS cabins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                sample_code TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                universe TEXT NOT NULL DEFAULT '',
                rank TEXT NOT NULL DEFAULT 'C',
                tags TEXT NOT NULL DEFAULT '',
                full_tags TEXT NOT NULL DEFAULT '',
                sedative_dose TEXT NOT NULL DEFAULT '0.000',
                upkeep TEXT NOT NULL DEFAULT '0.000',
                subscription_tier TEXT NOT NULL DEFAULT '',
                subscription_started_at TEXT NOT NULL DEFAULT '',
                recessive_name TEXT NOT NULL DEFAULT '',
                recessive_description TEXT NOT NULL DEFAULT '',
                dominants TEXT NOT NULL DEFAULT '[]',
                active INTEGER NOT NULL DEFAULT 1,
                note TEXT NOT NULL DEFAULT ''
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
        if "crew_vector_bonus" not in task_columns:
            self._add_column_if_missing(
                conn,
                "tasks",
                "crew_vector_bonus",
                "ALTER TABLE tasks ADD COLUMN crew_vector_bonus TEXT NOT NULL DEFAULT '0.000'",
            )
        transaction_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(transactions)").fetchall()
        }
        for column, statement in {
            "currency": "ALTER TABLE transactions ADD COLUMN currency TEXT NOT NULL DEFAULT 'ap'",
            "source": "ALTER TABLE transactions ADD COLUMN source TEXT NOT NULL DEFAULT ''",
            "purchase_id": "ALTER TABLE transactions ADD COLUMN purchase_id INTEGER",
            "real_ap_amount": "ALTER TABLE transactions ADD COLUMN real_ap_amount TEXT NOT NULL DEFAULT '0.000'",
            "shadow_ap_amount": "ALTER TABLE transactions ADD COLUMN shadow_ap_amount TEXT NOT NULL DEFAULT '0.000'",
        }.items():
            if column not in transaction_columns:
                self._add_column_if_missing(conn, "transactions", column, statement)
        conn.execute("UPDATE transactions SET currency = 'ap' WHERE currency = '' OR currency IS NULL")
        cabin_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(cabins)").fetchall()
        }
        for column, statement in {
            "sample_code": "ALTER TABLE cabins ADD COLUMN sample_code TEXT NOT NULL DEFAULT ''",
            "universe": "ALTER TABLE cabins ADD COLUMN universe TEXT NOT NULL DEFAULT ''",
            "full_tags": "ALTER TABLE cabins ADD COLUMN full_tags TEXT NOT NULL DEFAULT ''",
            "upkeep": "ALTER TABLE cabins ADD COLUMN upkeep TEXT NOT NULL DEFAULT '0.000'",
            "subscription_tier": "ALTER TABLE cabins ADD COLUMN subscription_tier TEXT NOT NULL DEFAULT ''",
            "subscription_started_at": "ALTER TABLE cabins ADD COLUMN subscription_started_at TEXT NOT NULL DEFAULT ''",
            "recessive_name": "ALTER TABLE cabins ADD COLUMN recessive_name TEXT NOT NULL DEFAULT ''",
            "recessive_description": "ALTER TABLE cabins ADD COLUMN recessive_description TEXT NOT NULL DEFAULT ''",
            "dominants": "ALTER TABLE cabins ADD COLUMN dominants TEXT NOT NULL DEFAULT '[]'",
        }.items():
            if column not in cabin_columns:
                self._add_column_if_missing(conn, "cabins", column, statement)
        conn.execute("UPDATE cabins SET full_tags = tags WHERE full_tags = ''")
        self._backfill_task_categories(conn)
        self._sync_task_categories(conn)

    def _crew_upkeep_discount_rate(self, conn: sqlite3.Connection) -> Decimal:
        return PRIME_UPKEEP_DISCOUNT_RATE if self._get_bool(conn, "prime_active") else Decimal("0.000")

    def _cabin_upkeep(
        self,
        row: sqlite3.Row,
        discount_rate: Decimal,
    ) -> dict[str, Decimal]:
        base = parse_ap(row["upkeep"] or "0.000")
        discount = parse_ap(base * discount_rate)
        effective = parse_ap(base - discount)
        return {
            "base": base,
            "discount": discount,
            "effective": effective,
        }

    def _cabin_dict(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        upkeep = self._cabin_upkeep(row, self._crew_upkeep_discount_rate(conn))
        item["base_upkeep"] = db_ap(upkeep["base"])
        item["upkeep_discount"] = db_ap(upkeep["discount"])
        item["effective_upkeep"] = db_ap(upkeep["effective"])
        item["dominant_max_level"] = self._dominant_max_level(conn, row["rank"])
        item["sr_promotion"] = self._quote_cabin_sr_promotion(conn, row)
        return item

    def _dominant_max_level(self, conn: sqlite3.Connection, rank: str) -> int:
        normalized = str(rank).strip().lower().replace("[", "").replace("]", "")
        if normalized.startswith("sr"):
            base = 4
        elif normalized.startswith("s"):
            base = 3
        elif normalized.startswith("a"):
            base = 2
        else:
            base = 1
        return base + (1 if self._get_bool(conn, "prime_active") else 0)

    def _quote_cabin_sr_promotion(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        cost = self._sr_subscription_cost(conn)
        base = {
            "available": False,
            "reason": "",
            "cost": db_ap(cost),
            "currency": AP,
            "required_dominant_level": SR_REQUIRED_DOMINANT_LEVEL,
        }
        rank = str(row["rank"] or "").strip().lower()
        if rank != "s":
            return {**base, "reason": "rank must be S"}
        if str(row["recessive_name"] or "").strip():
            return {**base, "reason": "recessive marker must be destroyed"}
        traits = self._dominant_traits(row["dominants"])
        if len(traits) < 3:
            return {**base, "reason": "three dominant traits are required"}
        if any(int(trait["level"]) < SR_REQUIRED_DOMINANT_LEVEL for trait in traits):
            return {**base, "reason": f"all dominants must be level {SR_REQUIRED_DOMINANT_LEVEL}"}
        balance = parse_ap(self._balance(conn, AP) + self._balance(conn, SHADOW_AP))
        if balance < cost:
            return {**base, "reason": "not enough AP or shadow AP"}
        return {**base, "available": True, "reason": ""}

    def _sr_subscription_cost(self, conn: sqlite3.Connection) -> Decimal:
        discount = self._crew_upkeep_discount_rate(conn)
        return parse_ap(SR_UPKEEP - parse_ap(SR_UPKEEP * discount))

    def _dominants_json(
        self,
        dominants: list[dict[str, Any]] | str | None,
        *,
        max_level: int | None = None,
    ) -> str:
        if dominants is None:
            return "[]"
        if isinstance(dominants, str):
            if max_level is None:
                return dominants.strip() or "[]"
            return self._dominants_json(
                self._dominant_traits(dominants),
                max_level=max_level,
            )
        normalized = []
        for dominant in dominants:
            name = str(dominant.get("name", "")).strip()
            if not name:
                continue
            try:
                level = int(dominant.get("level", 1))
            except (TypeError, ValueError):
                level = 1
            level = max(1, level)
            if max_level is not None and level > max_level:
                raise ValueError(
                    f"dominant level must not exceed {max_level} for this rank"
                )
            normalized.append({"name": name, "level": level})
        return json.dumps(normalized, ensure_ascii=False)

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
            "retro_buffer_cleared_task_id": "0",
            "prime_active": "0",
            "prime_active_since": "",
            "prime_weeks_purchased": "0",
            "prime_loyalty_weeks": "0",
            "neural_shard_pity": "0",
            "discount_start_base": db_ap(DEFAULT_DISCOUNT_START_BASE),
            "discount_purchase_cost": db_ap(DEFAULT_DISCOUNT_PURCHASE_COST),
            "historical_discount_cashback": db_ap(DEFAULT_HISTORICAL_DISCOUNT_CASHBACK),
            "retroactive_indexing_purchase_cost": db_ap(
                DEFAULT_HISTORICAL_RETROACTIVE_INDEXING_COST
            ),
            "historical_starting_balance": db_ap(DEFAULT_HISTORICAL_STARTING_BALANCE),
        }
        defaults.update({f"vector_level:{key}": "0" for key in UPGRADABLE_VECTORS})
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
