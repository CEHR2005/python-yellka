from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .money import parse_ap


@dataclass(frozen=True)
class Vector:
    key: str
    title: str


@dataclass(frozen=True)
class CatalogItem:
    key: str
    title: str
    value: Decimal


VECTORS: dict[str, Vector] = {
    "code": Vector("code", "Код/Движок"),
    "modeling": Vector("modeling", "3D-Моделирование"),
    "animation": Vector("animation", "Анимация/Риг"),
    "sfx": Vector("sfx", "SFX/Звук"),
    "gamedesign": Vector("gamedesign", "Геймдизайн/Логика"),
}


CATALOG_ITEMS: tuple[CatalogItem, ...] = (
    CatalogItem("player_search", "поиск игрока", parse_ap("0.77")),
    CatalogItem("move_to_player", "движение к игроку", parse_ap("0.77")),
    CatalogItem("melee", "ближний бой", parse_ap("0.77")),
    CatalogItem("special_attacks", "специальные атаки", parse_ap("0.825")),
    CatalogItem("summons_low", "призывы", parse_ap("0.825")),
    CatalogItem("damage_taken_dodep", "получение урона (Додеп)", parse_ap("0.825")),
    CatalogItem("stun_dodep", "оглушение (Додеп)", parse_ap("0.825")),
    CatalogItem("fear_dodep", "страх (Додеп)", parse_ap("0.825")),
    CatalogItem("apply_effects", "наложение эффектов", parse_ap("0.96")),
    CatalogItem("duration_refresh", "обновление длительности", parse_ap("0.96")),
    CatalogItem("pull", "Притягивание", parse_ap("0.96")),
    CatalogItem("push", "Расталкивание", parse_ap("0.96")),
    CatalogItem("hit", "Удар", parse_ap("1.02")),
    CatalogItem("projectile", "Снаряд", parse_ap("1.02")),
    CatalogItem("point_explosion", "Взрыв в точке", parse_ap("1.02")),
    CatalogItem("ground_wave", "Волна по земле", parse_ap("1.08")),
    CatalogItem("beam", "Луч", parse_ap("1.08")),
    CatalogItem("orb", "Орб", parse_ap("1.08")),
    CatalogItem("rain", "Дождь", parse_ap("1.08")),
    CatalogItem("area", "Область", parse_ap("1.08")),
    CatalogItem("fire", "Огонь", parse_ap("1.2")),
    CatalogItem("ice", "Лёд", parse_ap("1.2")),
    CatalogItem("lightning", "Молния", parse_ap("1.2")),
    CatalogItem("poison", "Яд", parse_ap("1.2")),
    CatalogItem("acid", "Кислота", parse_ap("1.3")),
    CatalogItem("darkness", "Тьма", parse_ap("1.3")),
    CatalogItem("light", "Свет", parse_ap("1.3")),
    CatalogItem("wind", "Ветер", parse_ap("1.3")),
    CatalogItem("stone", "Камень", parse_ap("1.4")),
    CatalogItem("blood", "Кровь", parse_ap("1.4")),
    CatalogItem("slow", "Замедление", parse_ap("1.5")),
    CatalogItem("binding", "Связывание", parse_ap("1.5")),
    CatalogItem("confusion", "Замешательство", parse_ap("1.5")),
    CatalogItem("stun", "Стан", parse_ap("1.6")),
    CatalogItem("fear", "Страх", parse_ap("1.6")),
    CatalogItem("mine", "Мина", parse_ap("1.8")),
    CatalogItem("zone", "Зона", parse_ap("1.8")),
    CatalogItem("healing", "Исцеление", parse_ap("1.9")),
    CatalogItem("regeneration", "Регенерация", parse_ap("1.9")),
    CatalogItem("vampirism", "Вампиризм", parse_ap("1.9")),
    CatalogItem("pierce", "Пробитие насквозь", parse_ap("2.0")),
    CatalogItem("time_slow", "Замедление времени", parse_ap("2.0")),
    CatalogItem("chain", "Цепь (Несколько целей)", parse_ap("2.1")),
    CatalogItem("split", "Разделение", parse_ap("2.1")),
    CatalogItem("returning", "Возвращение", parse_ap("2.1")),
    CatalogItem("summon", "Призыв", parse_ap("2.2")),
    CatalogItem("turret", "Туррель", parse_ap("2.2")),
    CatalogItem("totem", "Тотем", parse_ap("2.2")),
    CatalogItem("clone", "Клон", parse_ap("2.2")),
    CatalogItem("shield", "Щит", parse_ap("2.8")),
    CatalogItem("barrier", "Барьер", parse_ap("3.2")),
    CatalogItem("reflection", "Отражение", parse_ap("3.4")),
    CatalogItem("absorption", "Поглощение", parse_ap("3.5")),
)

CATALOG_BY_KEY = {item.key: item for item in CATALOG_ITEMS}


def find_catalog_item(query: str) -> CatalogItem:
    normalized = query.strip().casefold()
    if normalized in CATALOG_BY_KEY:
        return CATALOG_BY_KEY[normalized]
    matches = [
        item
        for item in CATALOG_ITEMS
        if normalized in item.title.casefold() or normalized in item.key.casefold()
    ]
    if not matches:
        raise KeyError(f"Catalog item not found: {query}")
    if len(matches) > 1:
        choices = ", ".join(item.key for item in matches[:8])
        raise KeyError(f"Catalog query is ambiguous: {query}. Matches: {choices}")
    return matches[0]


def require_vector(key: str) -> Vector:
    normalized = key.strip().casefold()
    if normalized not in VECTORS:
        choices = ", ".join(VECTORS)
        raise KeyError(f"Unknown vector: {key}. Available: {choices}")
    return VECTORS[normalized]
