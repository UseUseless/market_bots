import asyncio
import sys

from app.infrastructure.database.session import init_models
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.repositories import ConfigRepository


async def main():
    print("Создание таблиц БД...")
    await init_models()
    print("Таблицы успешно созданы (market_bots.db).")

    # Опционально: Добавить тестовые данные
    async with async_session_factory() as session:
        repo = ConfigRepository(session)

        # Проверяем, есть ли уже конфиги
        existing = await repo.get_active_strategies()
        if not existing:
            print("Добавляем тестовую стратегию в БД...")
            await repo.add_strategy_config({
                "exchange": "bybit",
                "instrument": "BTCUSDT",
                "interval": "1min",
                "strategy_name": "live_debug_strategy",
                "risk_manager_type": "FIXED",
                "parameters": {},
                "is_active": True
            })
            print("Тестовая стратегия добавлена.")
        else:
            print("Стратегии уже существуют в БД.")


if __name__ == "__main__":
    # Windows fix для asyncio loop
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())