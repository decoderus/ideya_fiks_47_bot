"""Кнопки и меню бота, по файлу на раздел.

Экраны обращаются к ним через пакет: `from app import keyboards as kb`, затем `kb.quest_card(...)`.
Новая клавиатура — в файл своего раздела; общее (клавиатура с «🏠 В меню», меню по ролям) — в base.
"""
from app.keyboards.achievements import *  # noqa: F403
from app.keyboards.base import *  # noqa: F403
from app.keyboards.broadcasts import *  # noqa: F403
from app.keyboards.coin_grants import *  # noqa: F403
from app.keyboards.journal import *  # noqa: F403
from app.keyboards.profile import *  # noqa: F403
from app.keyboards.questions import *  # noqa: F403
from app.keyboards.quests import *  # noqa: F403
from app.keyboards.rating import *  # noqa: F403
from app.keyboards.registration import *  # noqa: F403
from app.keyboards.review import *  # noqa: F403
from app.keyboards.school import *  # noqa: F403
from app.keyboards.seasons import *  # noqa: F403
from app.keyboards.shop import *  # noqa: F403
from app.keyboards.survey import *  # noqa: F403
