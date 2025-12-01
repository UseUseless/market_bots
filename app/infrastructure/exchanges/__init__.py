"""
Пакет адаптеров бирж (Exchange Adapters Layer).

Этот пакет содержит реализации интерфейсов взаимодействия с внешними торговыми площадками.
В терминологии Гексагональной архитектуры (Ports & Adapters), классы здесь являются
**Secondary (Driven) Adapters**, реализующими порты `BaseDataClient` и `BaseTradeClient`.

Основные задачи пакета:
1.  **Унификация API**: Преобразование специфичных для каждой биржи форматов данных
    (JSON, gRPC messages) в единые структуры домена (Pandas DataFrame, Event objects).
2.  **Управление соединением**: Аутентификация, подпись запросов, обработка сессий.
3.  **Соблюдение лимитов (Rate Limiting)**: Контроль частоты запросов, пагинация исторических данных.

Доступные реализации:
    - :class:`~.bybit.BybitHandler`: Адаптер для Bybit Unified Trading (Spot/Linear/Inverse).
    - :class:`~.tinkoff.TinkoffHandler`: Адаптер для Tinkoff Invest API (MOEX/SPB).
    - :class:`~.base.BaseExchangeHandler`: Базовый абстрактный класс с общей логикой.
"""