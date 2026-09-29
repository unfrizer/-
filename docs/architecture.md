# Архитектура

Frontend React Mini App вызывает FastAPI по `/api/v1`. Backend сохраняет профиль, планы и mock-уведомления в SQLite для локального demo. Matching Engine читает правила из `data/demo_support_programs.json`, возвращает `pass`, `fail`, `unknown`, score и статус. Платформенная интеграция в demo заменена Mock MAX: уведомление персистируется и тестируется без токена.

