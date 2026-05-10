# python-yellka

Python-бот и CLI для менеджмента баланса AP по правилам рабочего терминала ASSIR/Yellka из issue #1.

## Что умеет

- Ведет SQLite-журнал всех транзакций: доходы, расходы, награды за задачи, покупки улучшений, кэшбек и ретро-премии.
- Считает награды за выполненные задачи по базе `Ядра Вычислений`, вектору, тематическому приоритету, бонусу полного закрытия ТЗ и каталожному коэффициенту из приложенного TXT.
- Поддерживает покупки из документа: `Ядро Вычислений` `+0.05 AP`, векторные множители `+10%`, `Кэшбек-Шина` до 25%, `Ретроспективная Индексация`.
- Показывает список выполненных задач и историю транзакций.
- Может работать как Telegram bot без внешних Python-зависимостей.

## Быстрый старт

```bash
python -m yellka --db ./balance.sqlite3 init --initial-balance 2.085 --update-bonus
python -m yellka --db ./balance.sqlite3 earn 10 "Ручная корректировка"
python -m yellka --db ./balance.sqlite3 buy cashback
python -m yellka --db ./balance.sqlite3 buy core
python -m yellka --db ./balance.sqlite3 complete "Цепь и возврат" --catalog chain --units 3 --vector code --full-close
python -m yellka --db ./balance.sqlite3 balance
python -m yellka --db ./balance.sqlite3 tasks
python -m yellka --db ./balance.sqlite3 transactions
```

По умолчанию база хранится в `~/.local/share/yellka/balance.sqlite3`. Путь можно задать через `--db` или переменную `YELLKA_DB`.

## Telegram

```bash
export TELEGRAM_BOT_TOKEN="123:token"
python -m yellka --db ./balance.sqlite3 telegram
```

Поддерживаемые команды:

```text
/balance
/earn <amount> [note]
/spend <amount> [note]
/complete <title> [units]
/tasks
/history
/buy_core
/buy_vector [code|modeling|animation|sfx|gamedesign]
/buy_cashback
/buy_retro
```

## Правила расчета

Награда за задачу:

```text
units * base_rate * catalog_weight * vector_multiplier * priority_multiplier * full_close_multiplier
```

- `base_rate` начинается с `0.2 AP`.
- `buy core` стоит `current_base * 10` и повышает базу на `0.05 AP`.
- `buy vector code` повышает вектор на `+10%`; стоимость шага `new_level * 0.5 AP`, максимум `+100%`.
- `buy cashback` стоит `3 AP`, дает `+5%` кэшбека за покупки ядра и векторов, максимум `25%`.
- `buy retro` стоит `25 AP`. После этого каждая новая задача запускает перерасчет старых задач по текущей базе и начисляет разницу.
- `--priority` включает множитель `x2`.
- `--full-close` включает бонус полного закрытия ТЗ `+50%`.

## Разработка

```bash
PYTHONPATH=src python -m unittest discover -s tests
```
