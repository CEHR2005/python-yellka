# python-yellka

Python-бот и CLI для менеджмента баланса AP по правилам рабочего терминала ASSIR/Yellka из issue #1.

## Что умеет

- Ведет SQLite-журнал всех транзакций: доходы, расходы, награды за задачи, покупки улучшений, скидки и ретро-премии.
- Считает награды за выполненные задачи по базе `Ядра Вычислений`, вектору, тематическому приоритету, бонусу полного закрытия ТЗ и каталожному коэффициенту из приложенного TXT.
- Поддерживает Shop 3.0: терминальные улучшения, AP/shadow AP/shard-кошелек,
  PRIME, НОКТУР, hub/expedition/world/recreation/genesis покупки и историю
  покупок.
- Показывает список выполненных задач и историю транзакций.
- Для каждой задачи хранит исходную награду, текущую награду после ретро и статус получения премии.
- Поддерживает категории задач через формат `Категория: задача`, например `ИИ врагов: поиск игрока`.
- Позволяет закрывать категорию с начислением премии или снова открывать ее.
- Показывает исторический заработок: задачи считаются по исходным `reward`,
  ретро — по разнице `current_reward - reward`, а остальное остается в
  премиях/прочих начислениях.
- Может работать как Discord bot через `discord.py`.
- После сообщения о выполненной задаче в Discord показывает быстрые кнопки:
  новая задача, покупка ядра и открытие стартовой панели.

## Быстрый старт

```bash
python -m yellka --db ./balance.sqlite3 init --initial-balance 2.085 --update-bonus
python -m yellka --db ./balance.sqlite3 earn 10 "Ручная корректировка"
python -m yellka --db ./balance.sqlite3 buy cashback
python -m yellka --db ./balance.sqlite3 buy core
python -m yellka --db ./balance.sqlite3 complete "Цепь и возврат" --catalog chain --units 3 --vector code --full-close
python -m yellka --db ./balance.sqlite3 complete "ИИ врагов: поиск игрока"
python -m yellka --db ./balance.sqlite3 balance
python -m yellka --db ./balance.sqlite3 tasks
python -m yellka --db ./balance.sqlite3 premium list
python -m yellka --db ./balance.sqlite3 premium mark 1
python -m yellka --db ./balance.sqlite3 categories list
python -m yellka --db ./balance.sqlite3 categories done "ИИ врагов"
python -m yellka --db ./balance.sqlite3 categories open "ИИ врагов"
python -m yellka --db ./balance.sqlite3 transactions
```

По умолчанию база хранится в `~/.local/share/yellka/balance.sqlite3`. Путь можно задать через `--db` или переменную `YELLKA_DB`.

## Discord

```bash
DISCORD_BOT_TOKEN="token"  # в .env
YELLKA_DISCORD_STARTUP_GUILD_ID="544945355364630558"
YELLKA_DISCORD_STARTUP_CHANNEL_ID="1478044179765395579"
python -m yellka --db ./balance.sqlite3 discord
```

Для чтения текстовых команд включите Message Content Intent в настройках Discord
приложения. По умолчанию используется префикс `!`; его можно изменить через
`--prefix` или `YELLKA_DISCORD_PREFIX`.
Если заданы `YELLKA_DISCORD_STARTUP_GUILD_ID` и
`YELLKA_DISCORD_STARTUP_CHANNEL_ID`, бот при старте отправит терминал-панель
в указанный канал.

## Web tracker

Веб-интерфейс живет в `web/` и подключается к тому же SQLite-файлу, что CLI и
Discord-бот. API защищен bearer-token из `YELLKA_WEB_TOKEN`; если переменная не
задана, локальный dev-token равен `dev-token`.

Самый удобный запуск всего dev-стека:

```bash
./scripts/dev.sh
```

Скрипт подхватывает `.env`, запускает FastAPI и Vite, выбирает свободные порты
от `8001` и `5173`, прокидывает API proxy во фронтенд и останавливает оба
процесса по `Ctrl+C`.

Если задан `DISCORD_BOT_TOKEN`, web API будет дублировать покупки, сдачи задач
и премии закрытых категорий в Discord. Канал берется из
`YELLKA_DISCORD_TRANSACTION_CHANNEL_ID`, а если он не задан — из
`YELLKA_DISCORD_STARTUP_CHANNEL_ID`.

Полезные переопределения:

```bash
YELLKA_DB="./balance.sqlite3" YELLKA_WEB_TOKEN="dev-token" ./scripts/dev.sh
YELLKA_WEB_HOST="0.0.0.0" VITE_HOST="0.0.0.0" ./scripts/dev.sh
```

Ручной запуск, если нужно разделить процессы:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
YELLKA_WEB_TOKEN="dev-token" YELLKA_DB="./balance.sqlite3" .venv/bin/yellka-web
```

Во втором терминале:

```bash
cd web
npm install
npm run dev
```

Vite проксирует `/api` на `http://127.0.0.1:8000`. Для Cloudflare Tunnel
достаточно публиковать Vite dev server или собранный preview и оставить тот же
token.

Основной режим:

```text
!start
```

Бот покажет панель с кнопками. Для дохода, расхода, задачи и покупки вектора
достаточно нажать кнопку и ответить следующим сообщением:

```text
8,55 старт
2.5 покупка ассета
Цепь и возврат 3
code
```

Поддерживаемые команды:

```text
!start
!balance
!earn <amount> [note]
!spend <amount> [note]
!complete <title> [units]
!tasks
!premium
!premium mark <task_id>
!categories
!categories done <category>
!categories open <category>
!history
!buy_core
!buy_vector [code|modeling|animation|sfx|gamedesign]
!buy_cashback
!buy_retro
```

## Shop 3.0 migration

Старый баланс и история остаются в той же SQLite-базе и считаются обычным
`ap`. Новые транзакции дополнительно хранят валюту, источник, связь с покупкой
и split реального/shadow AP.

Главные изменения правил:

- `buy core` теперь стоит `current_base * 10` и повышает базу на `+0.05 AP`,
  если НОКТУР core rewrite не меняет шаг.
- `buy cashback` стоит `3 + current_level AP`, максимум 5 уровней.
- `buy vector` остается `next_level * 0.5 AP`, но максимум может расширяться
  НОКТУР limiter removal.
- `buy retro` больше не включает вечный автопересчет. Это одноразовый retro
  buffer: берутся последние eligible задачи, считается gross delta, комиссия
  `max(1 AP, gross * commission)`, и начисляется net только если gross > fee.
- Вектор `media` доступен для задач как непокупаемый task vector с `x2`
  payout-модификатором.

Web-first магазин доступен через вкладки интерфейса и API:

```text
/api/shop/catalog
/api/shop/quote
/api/shop/purchase
/api/shop/purchases
/api/wallet
/api/effects
/api/prime
/api/expeditions
/api/cabins
/api/prestige
```

## Правила расчета

Награда за задачу:

```text
units * base_rate * catalog_weight * vector_multiplier * priority_multiplier * full_close_multiplier
```

- Если название задачи записано как `Категория: задача`, бот отдельно сохранит
  категорию и название. Это нужно для очереди премий и группировки прошлых задач.
- Премия категории считается как `50%` от исходной стоимости задач в этой
  категории, то есть от суммы `reward`, без ретро-пересчета `current_reward`.
- `categories done` начисляет только еще не полученную часть премии категории
  и отмечает эти задачи как премированные, поэтому повторная команда не удвоит
  баланс.
- `base_rate` начинается с `0.2 AP`.
- `buy core` стоит `current_base * 10` с учетом купленной скидки и повышает базу на `0.05 AP`.
- `buy vector code` повышает вектор на `+10%`; стоимость шага `new_level * 0.5 AP`, максимум `+100%`.
- `buy cashback` стоит `3 + current_level AP`, дает `+5%` скидки на покупки ядра и векторов, максимум `25%`.
- `buy retro` активирует одноразовый retro buffer и не пересчитывает задачи автоматически после каждой новой задачи.
- `--priority` включает множитель `x2`.
- `--full-close` включает бонус полного закрытия ТЗ `+50%`.

## Разработка

```bash
PYTHONPATH=src python -m unittest discover -s tests
cd web && npm run lint && npm run build
```
