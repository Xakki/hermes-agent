"""Localized static descriptions for slash-command discovery surfaces.

The central command registry intentionally keeps English as its canonical source
text.  Messaging surfaces call :func:`localized_command_description` so the
Telegram command menu and gateway /help output can follow ``display.language``
without changing CLI autocomplete or command dispatch.
"""

from __future__ import annotations

from agent.i18n import get_language  # type: ignore[import-not-found]


_RUSSIAN_COMMAND_DESCRIPTIONS: dict[str, str] = {
    "start": "Подтвердить запуск платформы",
    "new": "Начать новый сеанс",
    "topic": "Настроить темы личного чата Telegram",
    "retry": "Повторить последнее сообщение",
    "undo": "Отменить последние ходы и продолжить",
    "title": "Задать название текущего сеанса",
    "branch": "Создать ветку текущего сеанса",
    "compress": "Сжать контекст беседы",
    "rollback": "Просмотреть или восстановить контрольную точку",
    "stop": "Остановить все фоновые процессы",
    "approve": "Одобрить ожидающую опасную команду",
    "deny": "Отклонить ожидающую опасную команду",
    "background": "Запустить запрос в фоне",
    "agents": "Показать активных агентов и задачи",
    "queue": "Поставить запрос в очередь на следующий ход",
    "steer": "Направить агента после следующего вызова инструмента",
    "goal": "Задать постоянную цель для Hermes",
    "moa": "Выполнить запрос через смесь агентов",
    "subgoal": "Управлять дополнительными критериями цели",
    "status": "Показать состояние сеанса, модели и контекста",
    "egress": "Показать состояние исходящего Docker-прокси",
    "context": "Показать подробное использование контекста",
    "whoami": "Показать ваши права на команды",
    "profile": "Показать активный профиль и его каталог",
    "sethome": "Сделать этот чат домашним каналом",
    "resume": "Продолжить ранее названный сеанс",
    "sessions": "Просмотреть и продолжить прошлые сеансы",
    "model": "Сменить модель для сеанса или глобально",
    "codex-runtime": "Переключить среду выполнения Codex",
    "personality": "Выбрать предустановленную личность",
    "diff": "Показать изменения Git в рабочем каталоге",
    "verbose": "Настроить показ хода работы инструментов",
    "footer": "Настроить служебный колонтитул ответов",
    "yolo": "Переключить режим без подтверждений",
    "approvals": "Настроить подтверждение опасных команд",
    "reasoning": "Настроить глубину и показ рассуждений",
    "fast": "Переключить быстрый режим модели",
    "voice": "Переключить голосовой режим",
    "skills": "Искать, устанавливать и настраивать навыки",
    "memory": "Проверить записи памяти и подтверждения",
    "bundles": "Показать наборы навыков",
    "learn": "Создать переиспользуемый навык",
    "init": "Создать или обновить инструкции AGENTS.md",
    "suggestions": "Просмотреть предлагаемые автоматизации",
    "blueprint": "Настроить автоматизацию по шаблону",
    "curator": "Управлять фоновым обслуживанием навыков",
    "kanban": "Открыть доску совместной работы профилей",
    "reload-mcp": "Перезагрузить MCP-серверы",
    "reload-skills": "Повторно просканировать установленные навыки",
    "commands": "Просмотреть все команды и навыки",
    "help": "Показать доступные команды",
    "restart": "Корректно перезапустить Hermes Gateway",
    "usage": "Показать расход токенов и лимиты",
    "topup": "Показать баланс Nous и управление оплатой",
    "insights": "Показать аналитику использования",
    "platform": "Приостановить или возобновить платформу",
    "update": "Обновить Hermes Agent до последней версии",
    "version": "Показать версию Hermes Agent",
    "debug": "Загрузить диагностический отчёт",
}


def localized_command_description(name: str, fallback: str) -> str:
    """Return the command description for the active display language."""
    if get_language() == "ru":
        return _RUSSIAN_COMMAND_DESCRIPTIONS.get(name, fallback)
    return fallback


def localized_alias_label(alias_count: int) -> str:
    """Return the label used before aliases in gateway help."""
    if get_language() == "ru":
        return "псевдоним" if alias_count == 1 else "псевдонимы"
    return "alias"


def localized_extension_description(kind: str, name: str, fallback: str) -> str:
    """Localize discovery text for dynamic plugins and skills.

    Their free-form descriptions are not part of Hermes's static catalog, so
    Russian discovery surfaces use a concise label plus the extension name.
    """
    if get_language() != "ru":
        return fallback
    if kind == "skill":
        return f"Запустить навык «{name}»"
    return f"Команда плагина «{name}»"


__all__ = [
    "localized_alias_label",
    "localized_command_description",
    "localized_extension_description",
]
