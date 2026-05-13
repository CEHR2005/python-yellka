from __future__ import annotations

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
from .money import db_ap, format_ap
from .service import EconomyError, EconomyService

DEFAULT_WEB_TOKEN = "dev-token"

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


class CabinCreate(BaseModel):
    name: str = Field(min_length=1)
    rank: str = "C"
    tags: str = ""
    note: str = ""


class PrestigeRequest(BaseModel):
    prime: bool = False


def create_app(
    db_path: str | Path | None = None,
    *,
    token: str | None = None,
    allow_origins: list[str] | None = None,
) -> FastAPI:
    app = FastAPI(title="Yellka Web API")
    app.state.db_path = Path(db_path) if db_path is not None else default_db_path()
    app.state.web_token = token if token is not None else os.environ.get(
        "YELLKA_WEB_TOKEN",
        DEFAULT_WEB_TOKEN,
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
            "effects": api_payload(service.list_effects()),
            "prime": service.prime_status(),
            "expeditions": service.list_expeditions(),
            "cabins": service.list_cabins(),
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
        return api_payload(service.set_category_completed(category, True))

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
        return service.submit_tracker_task(task_id)

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
        return api_payload(
            service.buy_shop_item(
                payload.item_key,
                target=payload.target,
                quantity=payload.quantity,
                note=payload.note,
                options=payload.options,
            )
        )

    @app.get("/api/shop/purchases", dependencies=[Depends(require_token)])
    async def shop_purchases(
        service: EconomyService = Depends(get_service),
    ) -> list[dict[str, Any]]:
        return api_payload(service.list_shop_purchases())

    @app.get("/api/effects", dependencies=[Depends(require_token)])
    async def effects(
        service: EconomyService = Depends(get_service),
    ) -> list[dict[str, Any]]:
        return api_payload(service.list_effects())

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

    @app.post("/api/prestige", dependencies=[Depends(require_token)])
    async def prestige(
        payload: PrestigeRequest,
        service: EconomyService = Depends(get_service),
    ) -> dict[str, Any]:
        return api_payload(service.run_prestige(prime=payload.prime))

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
