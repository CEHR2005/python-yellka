from __future__ import annotations

import logging
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .catalog import CATALOG_ITEMS, VECTORS
from .cli import default_db_path, load_dotenv
from .discord_notify import DiscordTransactionNotifier
from .money import db_ap, format_ap
from .service import EconomyError, EconomyService

DEFAULT_WEB_TOKEN = "dev-token"
DEFAULT_DISCORD_NOTIFIER = object()
logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)


class CategoryCreate(BaseModel):
    category: str = Field(min_length=1)


class TrackerTaskCreate(BaseModel):
    title: str = Field(min_length=1)
    category: str = ""
    vector: str = "code"
    units: int = Field(default=1, ge=1)
    catalog_key: str = ""
    catalog_value: str = "1"
    priority: bool = False
    full_close: bool = False
    note: str = ""


class TrackerTaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    category: str | None = None
    vector: str | None = None
    units: int | None = Field(default=None, ge=1)
    catalog_key: str | None = None
    catalog_value: str | None = None
    priority: bool | None = None
    full_close: bool | None = None
    note: str | None = None


class ShopQuoteRequest(BaseModel):
    item_key: str = Field(min_length=1)
    target: str = ""
    quantity: int = Field(default=1, ge=1)
    options: dict[str, Any] = Field(default_factory=dict)


class ShopPurchaseRequest(ShopQuoteRequest):
    note: str = ""


class ExpeditionCreate(BaseModel):
    title: str = Field(min_length=1)
    difficulty: str = "normal"
    note: str = ""


class DominantTraitPayload(BaseModel):
    name: str = Field(min_length=1)
    level: int = Field(default=1, ge=1)


class CabinCreate(BaseModel):
    name: str = Field(min_length=1)
    rank: str = "C"
    tags: str = ""
    sample_code: str = ""
    universe: str = ""
    full_tags: str = ""
    sedative_dose: str = "0"
    upkeep: str = "0"
    subscription_tier: str = ""
    subscription_started_at: str = ""
    recessive_name: str = ""
    recessive_description: str = ""
    dominants: list[DominantTraitPayload] = Field(default_factory=list)
    active: bool = True
    note: str = ""


class CabinUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    rank: str | None = None
    tags: str | None = None
    sample_code: str | None = None
    universe: str | None = None
    full_tags: str | None = None
    sedative_dose: str | None = None
    upkeep: str | None = None
    subscription_tier: str | None = None
    subscription_started_at: str | None = None
    recessive_name: str | None = None
    recessive_description: str | None = None
    dominants: list[DominantTraitPayload] | None = None
    active: bool | None = None
    note: str | None = None


class PrestigeRequest(BaseModel):
    prime: bool = False


def create_app(
    db_path: str | Path | None = None,
    *,
    token: str | None = None,
    allow_origins: list[str] | None = None,
    discord_notifier: Any = DEFAULT_DISCORD_NOTIFIER,
) -> FastAPI:
    app = FastAPI(title="Yellka Web API")
    app.state.db_path = Path(db_path) if db_path is not None else default_db_path()
    app.state.web_token = token if token is not None else os.environ.get(
        "YELLKA_WEB_TOKEN",
        DEFAULT_WEB_TOKEN,
    )
    app.state.discord_notifier = (
        DiscordTransactionNotifier.from_env()
        if discord_notifier is DEFAULT_DISCORD_NOTIFIER
        else discord_notifier
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins
        or [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def require_token(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(security),
        ],
    ) -> None:
        expected = app.state.web_token
        if not expected:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="YELLKA_WEB_TOKEN must not be empty",
            )
        if (
            credentials is None
            or credentials.scheme.lower() != "bearer"
            or credentials.credentials != expected
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def get_service() -> EconomyService:
        return EconomyService(app.state.db_path)

    async def notify_discord(content: str) -> None:
        notifier = app.state.discord_notifier
        if notifier is None:
            return
        try:
            await notifier.send(content)
        except Exception:
            logger.warning("Discord transaction notification failed", exc_info=True)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/bootstrap", dependencies=[Depends(require_token)])
    async def bootstrap(
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        return {
            "balance": state_payload(service),
            "categories": api_payload(service.list_categories()),
            "tasks": service.list_tracker_tasks(),
            "catalog": catalog_payload(),
            "vectors": vector_payload(),
            "wallet": api_payload(service.get_wallet()),
            "shop_catalog": api_payload(service.list_shop_catalog()),
            "shop_purchases": api_payload(service.list_shop_purchases()),
            "history": api_payload(service.list_history_entries()),
            "effects": api_payload(service.list_effects()),
            "prime": service.prime_status(),
            "crew_upkeep": api_payload(service.crew_upkeep_summary()),
            "expeditions": service.list_expeditions(),
            "cabins": service.list_cabins(),
            "retro_buffer": api_payload(service.get_retro_buffer()),
        }

    @app.get("/api/balance", dependencies=[Depends(require_token)])
    async def balance(
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        return state_payload(service)

    @app.get("/api/wallet", dependencies=[Depends(require_token)])
    async def wallet(
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        return api_payload(service.get_wallet())

    @app.get("/api/categories", dependencies=[Depends(require_token)])
    async def categories(
        service: EconomyService = Depends(get_service),
    ) -> Any:
        return api_payload(service.list_categories())

    @app.post("/api/categories", dependencies=[Depends(require_token)])
    async def create_category(
        payload: CategoryCreate,
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.create_task_category(payload.category)

    @app.post("/api/categories/{category}/complete", dependencies=[Depends(require_token)])
    async def complete_category(
        category: str,
        service: EconomyService = Depends(get_service),
    ) -> Any:
        result = service.set_category_completed(category, True)
        if parse_decimal_payload(result.get("premium_awarded")) > 0:
            await notify_discord(
                format_category_notification(result, service.get_wallet())
            )
        return api_payload(result)

    @app.post("/api/categories/{category}/reopen", dependencies=[Depends(require_token)])
    async def reopen_category(
        category: str,
        service: EconomyService = Depends(get_service),
    ) -> Any:
        return api_payload(service.set_category_completed(category, False))

    @app.get("/api/tasks", dependencies=[Depends(require_token)])
    async def tasks(
        service: EconomyService = Depends(get_service),
    ) -> list[dict[str, Any]]:
        return service.list_tracker_tasks()

    @app.post("/api/tasks", dependencies=[Depends(require_token)])
    async def create_task(
        payload: TrackerTaskCreate,
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.create_tracker_task(**payload.model_dump())

    @app.patch("/api/tasks/{task_id}", dependencies=[Depends(require_token)])
    async def update_task(
        task_id: int,
        payload: TrackerTaskUpdate,
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.update_tracker_task(
            task_id,
            **payload.model_dump(exclude_unset=True),
        )

    @app.post("/api/tasks/{task_id}/done", dependencies=[Depends(require_token)])
    async def mark_task_done(
        task_id: int,
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.mark_tracker_task_done(task_id)

    @app.post("/api/tasks/{task_id}/submit", dependencies=[Depends(require_token)])
    async def submit_task(
        task_id: int,
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        result = service.submit_tracker_task(task_id)
        await notify_discord(
            format_task_submit_notification(result, service.get_wallet())
        )
        return result

    @app.post("/api/tasks/{task_id}/revert-submit", dependencies=[Depends(require_token)])
    async def revert_task_submit(
        task_id: int,
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        result = service.revert_tracker_task_submission(task_id)
        await notify_discord(
            format_task_submit_revert_notification(result, service.get_wallet())
        )
        return api_payload(result)

    @app.get("/api/shop/catalog", dependencies=[Depends(require_token)])
    async def shop_catalog(
        service: EconomyService = Depends(get_service),
    ) -> list[dict[str, Any]]:
        return api_payload(service.list_shop_catalog())

    @app.post("/api/shop/quote", dependencies=[Depends(require_token)])
    async def shop_quote(
        payload: ShopQuoteRequest,
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        return api_payload(
            service.quote_shop_purchase(
                payload.item_key,
                target=payload.target,
                quantity=payload.quantity,
                options=payload.options,
            )
        )

    @app.post("/api/shop/purchase", dependencies=[Depends(require_token)])
    async def shop_purchase(
        payload: ShopPurchaseRequest,
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        result = service.buy_shop_item(
            payload.item_key,
            target=payload.target,
            quantity=payload.quantity,
            note=payload.note,
            options=payload.options,
        )
        await notify_discord(format_purchase_notification(result, service.get_wallet()))
        return api_payload(result)

    @app.get("/api/shop/purchases", dependencies=[Depends(require_token)])
    async def shop_purchases(
        service: EconomyService = Depends(get_service),
    ) -> list[dict[str, Any]]:
        return api_payload(service.list_shop_purchases())

    @app.get("/api/history", dependencies=[Depends(require_token)])
    async def history(
        service: EconomyService = Depends(get_service),
    ) -> list[dict[str, Any]]:
        return api_payload(service.list_history_entries())

    @app.get("/api/effects", dependencies=[Depends(require_token)])
    async def effects(
        service: EconomyService = Depends(get_service),
    ) -> list[dict[str, Any]]:
        return api_payload(service.list_effects())

    @app.get("/api/retro-buffer", dependencies=[Depends(require_token)])
    async def retro_buffer(
        service: EconomyService = Depends(get_service),
    ) -> Any:
        return api_payload(service.get_retro_buffer())

    @app.post("/api/retro-buffer/activate", dependencies=[Depends(require_token)])
    async def activate_retro_buffer(
        service: EconomyService = Depends(get_service),
    ) -> Any:
        purchase = service.buy_shop_item("terminal.retro_buffer", note="Retro tab")
        await notify_discord(format_purchase_notification(purchase, service.get_wallet()))
        return api_payload(service.get_retro_buffer())

    @app.get("/api/prime", dependencies=[Depends(require_token)])
    async def prime(
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.prime_status()

    @app.get("/api/expeditions", dependencies=[Depends(require_token)])
    async def expeditions(
        service: EconomyService = Depends(get_service),
    ) -> list[dict[str, Any]]:
        return service.list_expeditions()

    @app.post("/api/expeditions", dependencies=[Depends(require_token)])
    async def create_expedition(
        payload: ExpeditionCreate,
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.create_expedition(**payload.model_dump())

    @app.get("/api/cabins", dependencies=[Depends(require_token)])
    async def cabins(
        service: EconomyService = Depends(get_service),
    ) -> list[dict[str, Any]]:
        return service.list_cabins()

    @app.post("/api/cabins", dependencies=[Depends(require_token)])
    async def create_cabin(
        payload: CabinCreate,
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.create_cabin(**payload.model_dump())

    @app.patch("/api/cabins/{cabin_id}", dependencies=[Depends(require_token)])
    async def update_cabin(
        cabin_id: int,
        payload: CabinUpdate,
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.update_cabin(cabin_id, **payload.model_dump(exclude_unset=True))

    @app.post("/api/cabins/{cabin_id}/dominants/{dominant_index}/upgrade", dependencies=[Depends(require_token)])
    async def upgrade_cabin_dominant(
        cabin_id: int,
        dominant_index: int,
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        if dominant_index < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="dominant index must be at least 1",
            )
        updated = service.upgrade_cabin_dominant(cabin_id, dominant_index)
        await notify_discord(
            format_crew_dominant_notification(updated)
        )
        return updated

    @app.post("/api/cabins/{cabin_id}/defect/excise", dependencies=[Depends(require_token)])
    async def excise_cabin_defect(
        cabin_id: int,
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        updated = service.excise_cabin_defect(cabin_id)
        await notify_discord(format_defect_excision_notification(updated))
        return api_payload(updated)

    @app.post("/api/cabins/{cabin_id}/promote/sr", dependencies=[Depends(require_token)])
    async def promote_cabin_to_sr(
        cabin_id: int,
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        updated = service.promote_cabin_to_sr(cabin_id)
        await notify_discord(format_sr_promotion_notification(updated))
        return api_payload(updated)

    @app.delete("/api/cabins/{cabin_id}", dependencies=[Depends(require_token)])
    async def delete_cabin(
        cabin_id: int,
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.delete_cabin(cabin_id)

    @app.post("/api/prestige", dependencies=[Depends(require_token)])
    async def prestige(
        payload: PrestigeRequest,
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        result = service.run_prestige(prime=payload.prime)
        await notify_discord(format_prestige_notification(result, service.get_wallet()))
        return api_payload(result)

    @app.exception_handler(EconomyError)
    async def economy_error_handler(_request: Any, exc: EconomyError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(KeyError)
    async def key_error_handler(_request: Any, exc: KeyError):
        message = exc.args[0] if exc.args else str(exc)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": message},
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Any, exc: ValueError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    return app


def state_payload(service: EconomyService) -> dict[str, Any]:
    state = service.get_state()
    return {
        "balance": format_ap(state.balance),
        "base_rate": format_ap(state.base_rate),
        "cashback_level": state.cashback_level,
        "cashback_percent": state.cashback_level * 5,
        "retroactive_indexing_enabled": state.retroactive_indexing_enabled,
        "vector_levels": state.vector_levels,
        "next_core_cost": format_ap(service.quote_core_upgrade().final_cost),
    }


def catalog_payload() -> list[dict[str, str]]:
    return [
        {
            "key": item.key,
            "title": item.title,
            "value": format_ap(item.value),
        }
        for item in CATALOG_ITEMS
    ]


def vector_payload() -> list[dict[str, str]]:
    return [
        {
            "key": key,
            "title": vector.title,
        }
        for key, vector in VECTORS.items()
    ]


def parse_decimal_payload(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.000")
    return Decimal(str(value))


def display_amount(value: Any) -> str:
    return format_ap(parse_decimal_payload(value))


def wallet_balance(wallet: dict[str, Any], currency: str) -> str:
    currencies = wallet.get("currencies", {})
    return display_amount(currencies.get(currency, "0.000"))


def format_task_submit_notification(
    task: dict[str, Any],
    wallet: dict[str, Any],
) -> str:
    title = str(task.get("title") or "Задача")
    category = str(task.get("category") or "").strip()
    full_title = f"{category}: {title}" if category else title
    reward = display_amount(task.get("submitted_reward") or "0.000")
    task_id = task.get("id")
    lines = [
        "Yellka: сдача задачи",
        f"#{task_id} {full_title}",
        f"Начислено: +{reward} AP",
    ]
    calculation = task.get("reward_calculation")
    if isinstance(calculation, dict):
        lines.extend(format_reward_calculation_lines(calculation))
    lines.append(f"Баланс AP: {wallet_balance(wallet, 'ap')}")
    return "\n".join(lines)


def format_task_submit_revert_notification(
    task: dict[str, Any],
    wallet: dict[str, Any],
) -> str:
    title = str(task.get("title") or "Задача")
    category = str(task.get("category") or "").strip()
    full_title = f"{category}: {title}" if category else title
    amount = display_amount(task.get("reverted_total") or "0.000")
    task_id = task.get("id")
    economy_task_id = task.get("reverted_economy_task_id")
    lines = [
        "Yellka: откат сдачи задачи",
        f"#{task_id} {full_title}",
        f"Откат начисления: -{amount} AP",
        f"Статус: done",
        f"Баланс AP: {wallet_balance(wallet, 'ap')}",
    ]
    if economy_task_id:
        lines.insert(2, f"Economy task: #{economy_task_id}")
    return "\n".join(lines)


def format_reward_calculation_lines(calculation: dict[str, Any]) -> list[str]:
    units = int(calculation.get("units") or 0)
    base_rate = display_amount(calculation.get("base_rate") or "0.000")
    core_base = display_amount(calculation.get("core_base_rate") or base_rate)
    crew_base = display_amount(calculation.get("crew_base_bonus") or "0.000")
    task_weight = display_amount(calculation.get("catalog_weight") or "1.000")
    purchased_vector = display_amount(
        calculation.get("purchased_vector_multiplier") or "1.000"
    )
    vector = display_amount(calculation.get("vector_multiplier") or "1.000")
    priority = display_amount(calculation.get("priority_multiplier") or "1.000")
    full_close = display_amount(calculation.get("full_close_bonus") or "1.000")
    crew_vector = display_amount(calculation.get("crew_vector_bonus") or "0.000")
    base_vector_total = display_amount(calculation.get("base_vector_total") or "0.000")
    full_close_premium = display_amount(
        calculation.get("full_close_premium") or "0.000"
    )
    reward = display_amount(calculation.get("reward") or "0.000")
    lines = ["Расчет:"]
    if crew_base != "0":
        lines.append(f"База: {base_rate} AP = {core_base} AP Ядро + {crew_base} AP crew")
    else:
        lines.append(f"База: {base_rate} AP")
    if crew_vector != "0":
        lines.append(f"Вектор: x{vector} = x{purchased_vector} куплено + x{crew_vector} crew")
    elif vector != "1":
        lines.append(f"Вектор: x{vector}")
    if task_weight != "1":
        lines.append(f"Вес задачи: x{task_weight}")
    if priority != "1":
        lines.append(f"Приоритет: x{priority}")
    lines.extend(format_crew_bonus_detail_lines(calculation))
    factors = [f"{units}u", f"{base_rate} AP"]
    if task_weight != "1":
        factors.append(f"x{task_weight}")
    if vector != "1":
        factors.append(f"x{vector}")
    if priority != "1":
        factors.append(f"x{priority}")
    lines.append(f"Основное: {' * '.join(factors)} = {base_vector_total} AP")
    if full_close != "1":
        lines.append(
            f"Полное закрытие: премия x{full_close} = +{full_close_premium} AP"
        )
    lines.append(f"Итого: {reward} AP")
    return [line for line in lines if line]


def format_crew_bonus_detail_lines(calculation: dict[str, Any]) -> list[str]:
    raw_details = calculation.get("crew_bonus_details")
    if not isinstance(raw_details, list):
        return []
    lines = []
    for detail in raw_details:
        if not isinstance(detail, dict):
            continue
        label = str(detail.get("label") or "Crew")
        source = str(detail.get("source") or "crew")
        trait = str(detail.get("trait") or "").strip()
        amount = display_amount(detail.get("amount") or "0.000")
        formula = str(detail.get("formula") or "").strip()
        title = f"{label} {source}"
        if trait:
            title = f"{title} ({trait})"
        lines.append(f"{title}: +{amount} = {formula}")
    return lines


def format_purchase_notification(
    purchase: dict[str, Any],
    wallet: dict[str, Any],
) -> str:
    title = str(purchase.get("title") or purchase.get("item_key") or "Покупка")
    currency = str(purchase.get("currency") or "ap")
    cost = display_amount(purchase.get("final_cost") or "0.000")
    target = str(purchase.get("target") or "").strip()
    quantity = int(purchase.get("quantity") or 1)
    lines = [
        "Yellka: покупка",
        title,
        f"Списано: -{cost} {currency}",
        f"Баланс {currency}: {wallet_balance(wallet, currency)}",
    ]
    if target:
        lines.insert(2, f"Цель: {target}")
    if quantity != 1:
        lines.insert(2, f"Количество: {quantity}")
    return "\n".join(lines)


def format_category_notification(
    category: dict[str, Any],
    wallet: dict[str, Any],
) -> str:
    name = str(category.get("category") or "Категория")
    premium = display_amount(category.get("premium_awarded") or "0.000")
    task_count = int(category.get("premium_task_count") or 0)
    return "\n".join(
        [
            "Yellka: закрытие категории",
            name,
            f"Премия: +{premium} AP",
            f"Задач премировано: {task_count}",
            f"Баланс AP: {wallet_balance(wallet, 'ap')}",
        ]
    )


def format_prestige_notification(
    prestige: dict[str, Any],
    wallet: dict[str, Any],
) -> str:
    refund = display_amount(prestige.get("refund") or "0.000")
    refund_currency = str(prestige.get("refund_currency") or "shadow_ap")
    shards = int(prestige.get("singularity_shards") or 0)
    lines = [
        "Yellka: сингулярный коллапс",
        f"Потрачено учтено: {display_amount(prestige.get('spent', '0.000'))} AP",
    ]
    if parse_decimal_payload(refund) > 0:
        lines.append(f"Возврат: +{refund} {refund_currency}")
        lines.append(f"Баланс {refund_currency}: {wallet_balance(wallet, refund_currency)}")
    if shards:
        lines.append(f"Осколки: +{shards} singularity_shard")
        lines.append(f"Баланс singularity_shard: {wallet_balance(wallet, 'singularity_shard')}")
    return "\n".join(lines)


def cabin_dominants(cabin: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(str(cabin.get("dominants") or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    dominants: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        try:
            level = int(item.get("level") or 1)
        except (TypeError, ValueError):
            level = 1
        dominants.append({"name": name, "level": max(1, level)})
    return dominants


def format_crew_dominant_notification(cabin: dict[str, Any]) -> str:
    sample_code = str(cabin.get("sample_code") or "").strip()
    name = str(cabin.get("name") or "Sample").strip()
    universe = str(cabin.get("universe") or "").strip()
    label = f"#{sample_code} {name}" if sample_code else name
    if universe:
        label = f"{label} ({universe})"
    cost = display_amount(cabin.get("upgrade_cost") or "0.000")
    balance_before = display_amount(cabin.get("balance_before") or "0.000")
    balance_after = display_amount(cabin.get("balance_after") or "0.000")
    shadow_before = display_amount(cabin.get("shadow_balance_before") or "0.000")
    shadow_after = display_amount(cabin.get("shadow_balance_after") or "0.000")
    dominant_name = str(cabin.get("dominant_name") or "Dominant")
    level_before = int(cabin.get("level_before") or 1)
    level_after = int(cabin.get("level_after") or level_before)
    lines = [
        "Yellka: прокачка черты экипажа",
        label,
        f"{dominant_name}: Lv.{level_before} -> Lv.{level_after}",
        f"Списано: -{cost} AP",
        f"Баланс AP: {balance_before} -> {balance_after}",
    ]
    if shadow_before != shadow_after:
        lines.append(f"Баланс shadow_ap: {shadow_before} -> {shadow_after}")
    return "\n".join(lines)


def format_defect_excision_notification(cabin: dict[str, Any]) -> str:
    sample_code = str(cabin.get("sample_code") or "").strip()
    name = str(cabin.get("name") or "Sample").strip()
    label = f"#{sample_code} {name}" if sample_code else name
    defect = str(cabin.get("excised_defect") or "Недостаток")
    cost = display_amount(cabin.get("excision_cost") or "0.000")
    currency = str(cabin.get("excision_currency") or "ap")
    balance_before = display_amount(cabin.get("balance_before") or "0.000")
    balance_after = display_amount(cabin.get("balance_after") or "0.000")
    return "\n".join(
        [
            "Yellka: иссечение недостатка",
            label,
            defect,
            f"Списано: -{cost} {currency}",
            f"Баланс {currency}: {balance_before} -> {balance_after}",
        ]
    )


def format_sr_promotion_notification(cabin: dict[str, Any]) -> str:
    sample_code = str(cabin.get("sample_code") or "").strip()
    name = str(cabin.get("name") or "Sample").strip()
    label = f"#{sample_code} {name}" if sample_code else name
    cost = display_amount(cabin.get("promotion_cost") or "0.000")
    ap_before = display_amount(cabin.get("ap_balance_before") or "0.000")
    ap_after = display_amount(cabin.get("ap_balance_after") or "0.000")
    shadow_before = display_amount(cabin.get("shadow_balance_before") or "0.000")
    shadow_after = display_amount(cabin.get("shadow_balance_after") or "0.000")
    return "\n".join(
        [
            "Yellka: повышение ранга",
            label,
            f"S -> SR, подписка: -{cost} AP",
            f"Баланс AP: {ap_before} -> {ap_after}",
            f"Баланс shadow_ap: {shadow_before} -> {shadow_after}",
        ]
    )


def api_payload(value: Any) -> Any:
    if isinstance(value, Decimal):
        return db_ap(value)
    if isinstance(value, list):
        return [api_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: api_payload(item) for key, item in value.items()}
    return value


def app() -> FastAPI:
    load_dotenv()
    return create_app()


def main() -> None:
    load_dotenv()
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "uvicorn is required to run the web API. Install project dependencies first."
        ) from exc
    uvicorn.run(
        "yellka.web_api:app",
        factory=True,
        host=os.environ.get("YELLKA_WEB_HOST", "127.0.0.1"),
        port=int(os.environ.get("YELLKA_WEB_PORT", "8000")),
        reload=os.environ.get("YELLKA_WEB_RELOAD", "0") == "1",
    )


if __name__ == "__main__":
    main()
