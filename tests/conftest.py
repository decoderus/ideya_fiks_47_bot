import pytest

from app.services import quests as quests_service

# Настоящие ограничения, сохранены до подмены
REAL_FIXED_RANGES = dict(quests_service.FIXED_RANGES)


@pytest.fixture(autouse=True)
def free_quest_rewards(monkeypatch):
    """Большинство тестов создают квесты от имени учителя ради проверки общей механики
    (автоподтверждение, серии, архив). Ограничения FIXED_RANGES в них отключены;
    тесты самих ограничений включают их фикстурой fixed_ranges."""
    monkeypatch.setattr(quests_service, "FIXED_RANGES", {})


@pytest.fixture
def fixed_ranges(monkeypatch):
    monkeypatch.setattr(quests_service, "FIXED_RANGES", REAL_FIXED_RANGES)
    return REAL_FIXED_RANGES
