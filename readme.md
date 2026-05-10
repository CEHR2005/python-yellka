# python-yellka

Python-бот и CLI для менеджмента баланса AP по правилам рабочего терминала ASSIR/Yellka из issue #1.

## Что умеет

- Ведет SQLite-журнал всех транзакций: доходы, расходы, награды за задачи, покупки улучшений, скидки и ретро-премии.
- Считает награды за выполненные задачи по базе `Ядра Вычислений`, вектору, тематическому приоритету, бонусу полного закрытия ТЗ и каталожному коэффициенту из приложенного TXT.
- Поддерживает покупки из документа: `Ядро Вычислений` `+0.05 AP`, векторные множители `+10%`, скидку до 25%, `Ретроспективная Индексация`.
- Показывает список выполненных задач и историю транзакций.
- Для каждой задачи хранит исходную награду, текущую награду после ретро и статус получения премии.
- Показывает исторический заработок: задачи считаются по исходным `reward`,
  ретро — по разнице `current_reward - reward`, а остальное остается в
  премиях/прочих начислениях.
- Может работать как Discord bot через `discord.py`.

## Быстрый старт

```bash
python -m yellka --db ./balance.sqlite3 init --initial-balance 2.085 --update-bonus
python -m yellka --db ./balance.sqlite3 earn 10 "Ручная корректировка"
python -m yellka --db ./balance.sqlite3 buy cashback
python -m yellka --db ./balance.sqlite3 buy core
python -m yellka --db ./balance.sqlite3 complete "Цепь и возврат" --catalog chain --units 3 --vector code --full-close
python -m yellka --db ./balance.sqlite3 balance
python -m yellka --db ./balance.sqlite3 tasks
python -m yellka --db ./balance.sqlite3 premium list
python -m yellka --db ./balance.sqlite3 premium mark 1
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
!history
!buy_core
!buy_vector [code|modeling|animation|sfx|gamedesign]
!buy_cashback
!buy_retro
```

## Правила расчета

Награда за задачу:

```text
units * base_rate * catalog_weight * vector_multiplier * priority_multiplier * full_close_multiplier
```

- `base_rate` начинается с `0.2 AP`.
- `buy core` стоит `current_base * 8` с учетом купленной скидки и повышает базу на `0.05 AP`.
- `buy vector code` повышает вектор на `+10%`; стоимость шага `new_level * 0.5 AP`, максимум `+100%`.
- `buy cashback` стоит `3 AP`, дает `+5%` скидки на покупки ядра и векторов, максимум `25%`.
- `buy retro` стоит `25 AP`. После этого каждая новая задача запускает перерасчет старых задач по текущей базе и начисляет разницу.
- `--priority` включает множитель `x2`.
- `--full-close` включает бонус полного закрытия ТЗ `+50%`.

## Разработка

```bash
PYTHONPATH=src python -m unittest discover -s tests
```
