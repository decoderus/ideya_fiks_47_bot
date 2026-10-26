"""Обезличенная копия базы для демонстрации (жюри, презентации).

    python deploy/anonymize.py <рабочая_копия.db> <демо.db>

Исходный файл не меняется. В копии:
- ФИО, имена и ники пользователей — вымышленные, номера MAX — несуществующие
  (app.demo.FAKE_ID_BASE + id): бот не сможет написать ни одному реальному человеку;
- ответы учеников: фото, видео и тексты убраны (ответы «Вопроса дня» — вопрос и вариант — остаются);
- тексты вопросов учеников по квестам и ответов на них заменены пометками;
- тексты записей «Журнала успехов» и повторяющие их причины начислений заменены пометками;
- голосовые и фото оповещений, короткие тестовые оповещения, ссылки на сообщения в чатах,
  название школы и id её канала — удалены;
- на месте убранного — пометка 🔒: что там было и почему скрыто (персональные данные, 152-ФЗ);
- описания, где встречается фамилия реального пользователя, заменены;
- добавлено несколько вымышленных ответов «на проверке», чтобы было что проверить.
Квесты, награды, классы, балансы, история монет, серии, достижения, сезоны остаются.
В конце VACUUM INTO: удалённые данные не остаются в файле.
"""
import json
import random
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.demo import FAKE_ID_BASE  # noqa: E402

SURNAMES = [
    "Иванов", "Смирнов", "Кузнецов", "Попов", "Васильев", "Петров", "Соколов", "Михайлов",
    "Новиков", "Фёдоров", "Морозов", "Волков", "Алексеев", "Лебедев", "Семёнов", "Егоров",
    "Павлов", "Козлов", "Степанов", "Николаев", "Орлов", "Андреев", "Макаров", "Никитин",
    "Захаров", "Зайцев", "Соловьёв", "Борисов", "Яковлев", "Григорьев", "Романов", "Воробьёв",
    "Сергеев", "Кузьмин", "Фролов", "Александров", "Дмитриев", "Королёв", "Гусев", "Киселёв",
    "Ильин", "Максимов", "Поляков", "Сорокин", "Виноградов", "Ковалёв", "Белов", "Медведев",
    "Антонов", "Тарасов", "Жуков", "Баранов", "Филиппов", "Комаров", "Давыдов", "Беляев",
    "Герасимов", "Богданов", "Осипов", "Сидоров", "Матвеев", "Титов", "Марков", "Миронов",
]
MALE = [
    "Александр", "Максим", "Артём", "Михаил", "Даниил", "Иван", "Дмитрий", "Кирилл", "Андрей",
    "Егор", "Никита", "Илья", "Алексей", "Матвей", "Тимофей", "Роман", "Владимир", "Ярослав",
    "Фёдор", "Георгий", "Константин", "Лев", "Николай", "Степан", "Марк", "Глеб",
]
FEMALE = [
    "Анастасия", "Мария", "Анна", "Виктория", "Екатерина", "Полина", "Софья", "Дарья",
    "Александра", "Ксения", "Елизавета", "Алиса", "Варвара", "Вероника", "Арина", "Ульяна",
    "Валерия", "Милана", "Алёна", "Кира", "Ева", "Василиса", "Таисия", "Злата",
]
# Мужские имена на -а/-я: по окончанию пол не угадать
MALE_ON_A = {"никита", "илья", "кузьма", "савва", "фома", "лука", "данила", "миша", "саша",
             "женя", "ваня", "дима", "коля", "петя", "паша", "серёжа", "сережа", "лёша", "леша",
             "гоша", "вова", "слава", "костя", "тёма", "тема", "егорка"}

# Пометки на месте убранного: что там было и почему убрано
PRIVACY = "Это персональные данные: школа не может передавать их без согласия родителей (152-ФЗ)."
HIDDEN_KINDS = {"photo": "фото", "video": "видео", "text": "текст"}
HIDDEN_TEXT = (
    "🔒 Описание скрыто в демо-версии: в нём упоминался реальный человек. " + PRIVACY
)
HIDDEN_QUESTION = (
    "🔒 Здесь был вопрос ученика по квесту. В демо-версии он скрыт: в тексте могут быть "
    "имена и личные подробности. " + PRIVACY
)
HIDDEN_REPLY = (
    "🔒 Здесь был ответ на вопрос ученика. В демо-версии он скрыт: в нём могут быть имена "
    "и личные подробности. Автор ответа и его роль сохранены."
)
HIDDEN_SUCCESS = (
    "🔒 Здесь была запись учителя об успехе ученика. В демо-версии текст скрыт: в нём могут быть "
    "имена и личные подробности. Категория, автор и дата сохранены."
)
HIDDEN_SURVEY = (
    "🔒 Здесь было пожелание из опроса. В демо-версии оно скрыто: в тексте могут быть имена "
    "и личные подробности. Ответы на вопросы с кнопками сохранены."
)
HIDDEN_REASON = "🔒 Причина скрыта в демо-версии: в ней упоминался реальный человек"
HIDDEN_COMMENT = "🔒 Комментарий скрыт в демо-версии: в нём упоминался реальный человек"
DEMO_EXAMPLE = "🧪 Пример ответа, придуман для демо-версии:\n"


def hidden_answer(kinds: list[str]) -> str:
    """«🔒 Здесь был ответ ученика: фото и текст. …»"""
    what = " и ".join(HIDDEN_KINDS[k] for k in kinds) or "ответ"
    reasons = []
    media = [HIDDEN_KINDS[k] for k in kinds if k in ("photo", "video")]
    if media:
        reasons.append(f"на {' и '.join(media)} — реальные школьники")
    if "text" in kinds or not kinds:
        reasons.append("в тексте могут быть имена и личные подробности")
    return (
        f"🔒 Здесь был ответ ученика: {what}. В демо-версии он скрыт: {', '.join(reasons)}. "
        f"{PRIVACY} Статус проверки, начисленные монеты и комментарий проверяющего сохранены."
    )


def broadcast_note(removed: list[str]) -> str:
    """Пометка в конце оповещения, из которого убраны фото или голосовое."""
    several = len(removed) > 1
    return (
        f"\n\n🔒 К оповещению {'были приложены' if several else 'было приложено'} "
        f"{' и '.join(removed)} — в демо-версии убрано: там могли быть реальные люди. {PRIVACY}"
    )
SHORT_BROADCAST = 30  # оповещения короче — тестовые, удаляются
# Вымышленные ответы на проверку: (название квеста, текст). Берутся первые PENDING_COUNT
# для открытых квестов — остальные запасные, на случай если школа какие-то квесты закрыла
PENDING_COUNT = 4
PENDING = [
    ("Новые английские слова",
     "1. reliable — надёжный — My friend is very reliable.\n2. curious — любопытный — Cats are curious."
     "\n3. borrow — брать взаймы — Can I borrow your pen?"),
    ("Школьный корреспондент",
     "В пятницу наш класс ездил на экскурсию в краеведческий музей. Мы узнали, как жили люди "
     "в нашем районе сто лет назад, и сами попробовали прясть шерсть. Больше всего понравился зал "
     "с военными письмами. Экскурсовод рассказала, что многие экспонаты принесли жители района."),
    ("Идея для школы",
     "Поставить в рекреации второго этажа полку буккроссинга: ученики приносят прочитанные книги "
     "и берут другие. Нужна одна полка и табличка с правилами — могу сделать её на технологии."),
    ("Получить «5» по русскому языку", "Получил пятёрку за самостоятельную по словосочетаниям."),
    ("Помощник дома",
     "Сегодня сам приготовил ужин на всю семью: гречку с котлетами и овощной салат. "
     "Мама сказала, что котлеты получились даже лучше, чем у неё."),
    ("Моё достижение",
     "Диплом II степени муниципального этапа олимпиады по математике, 18 октября 2026 года."),
]


def is_female(full_name: str) -> bool:
    parts = full_name.split()
    first = (parts[1] if len(parts) > 1 else parts[0]).lower()
    return first.endswith(("а", "я")) and first not in MALE_ON_A


def fake_name(rng: random.Random, female: bool, used: set[str]) -> tuple[str, str]:
    while True:
        surname = rng.choice(SURNAMES) + ("а" if female else "")
        first = rng.choice(FEMALE if female else MALE)
        full = f"{surname} {first}"
        if full not in used:
            used.add(full)
            return full, first


def stems(full_names: list[str]) -> set[str]:
    """Основы реальных фамилий для поиска в свободном тексте: «Зарубин» найдёт и «Зарубина».

    Берутся все слова ФИО (порядок «Имя Фамилия» тоже встречается), кроме совпадающих
    со списками вымышленных имён и фамилий: обычное «Иванов» или «Кирилл» человека не выдаёт.
    """
    common = [w.lower() for w in SURNAMES + MALE + FEMALE]
    result = set()
    for name in full_names:
        for word in name.split():
            if len(word) < 4:
                continue
            stem = (word[:-1] if len(word) >= 6 else word).lower()
            if not any(c.startswith(stem) or stem.startswith(c) for c in common):
                result.add(stem)
    return result


def anonymize(src: str, dst: str, seed: int = 47) -> dict:
    rng = random.Random(seed)
    db = sqlite3.connect(src)
    # Работаем в памяти: исходный файл не меняется
    mem = sqlite3.connect(":memory:")
    db.backup(mem)
    db.close()
    db = mem
    stats: dict[str, int] = {}

    real = [n for (n,) in db.execute("SELECT full_name FROM users WHERE full_name IS NOT NULL")]
    real += [n for (n,) in db.execute("SELECT full_name FROM enrollment_log WHERE full_name IS NOT NULL")]
    real += [n for (n,) in db.execute("SELECT full_name FROM teacher_requests WHERE full_name IS NOT NULL")]
    real_stems = stems(real)

    # Пользователи
    used: set[str] = set()
    names: dict[int, str] = {}
    for user_id, full_name, role in db.execute(
        "SELECT id, full_name, role FROM users ORDER BY id"
    ).fetchall():
        full, first = fake_name(rng, is_female(full_name or "А Б"), used)
        names[user_id] = full
        db.execute(
            "UPDATE users SET full_name = CASE WHEN full_name IS NULL THEN NULL ELSE ? END, "
            "name = ?, max_user_id = ?, state = NULL, state_data = NULL WHERE id = ?",
            (full, first, FAKE_ID_BASE + user_id, user_id),
        )
    stats["users"] = len(names)
    for table in ("enrollment_log", "teacher_requests", "season_results"):
        for row_id, user_id, full_name in db.execute(
            f"SELECT id, user_id, full_name FROM {table}"
        ).fetchall():
            new = names.get(user_id) or fake_name(rng, is_female(full_name or "А Б"), used)[0]
            db.execute(f"UPDATE {table} SET full_name = ? WHERE id = ?", (new, row_id))

    # Ответы учеников: на месте убранного — пометка, что именно там было
    rows = db.execute(
        "SELECT id, photo, video, text FROM submissions WHERE question_id IS NULL"
    ).fetchall()
    for sub_id, photo, video, text in rows:
        kinds = [k for k, v in (("photo", photo), ("video", video), ("text", text))
                 if v and v != "null"]
        db.execute("UPDATE submissions SET text = ? WHERE id = ?", (hidden_answer(kinds), sub_id))
    db.execute(
        "UPDATE submissions SET photo = NULL, video = NULL, publication_draft = NULL, "
        "publication_note = NULL"
    )
    stats["submissions"] = len(rows)

    # Вопросы учеников по квестам и ответы на них (таблиц нет в базах до миграции 0026)
    tables = {t for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "quest_questions" in tables:
        stats["questions"] = db.execute("UPDATE quest_questions SET text = ?", (HIDDEN_QUESTION,)).rowcount
        db.execute("UPDATE quest_answers SET text = ?", (HIDDEN_REPLY,))
    if "successes" in tables:
        stats["successes"] = db.execute("UPDATE successes SET text = ?", (HIDDEN_SUCCESS,)).rowcount
        # Причина начисления «🌟 Успех: <текст записи>» повторяет скрытый текст
        db.execute(
            "UPDATE coin_transactions SET reason = '🌟 Успех (запись скрыта в демо-версии)' "
            "WHERE reason LIKE '🌟 Успех:%'"
        )
    if "survey_responses" in tables:
        stats["survey"] = db.execute(
            "UPDATE survey_responses SET comment = ? WHERE comment IS NOT NULL", (HIDDEN_SURVEY,)
        ).rowcount

    # Оповещения
    short = [bid for (bid,) in db.execute(
        "SELECT id FROM broadcasts WHERE length(trim(coalesce(text, ''))) < ?", (SHORT_BROADCAST,)
    )]
    for bid in short:
        db.execute("DELETE FROM broadcast_recipients WHERE broadcast_id = ?", (bid,))
        db.execute("UPDATE broadcast_drafts SET broadcast_id = NULL WHERE broadcast_id = ?", (bid,))
        db.execute("DELETE FROM broadcasts WHERE id = ?", (bid,))
    stats["broadcasts_deleted"] = len(short)
    db.execute(
        "DELETE FROM broadcast_drafts WHERE length(trim(coalesce(text, ''))) < ?", (SHORT_BROADCAST,)
    )
    for table in ("broadcasts", "broadcast_drafts"):
        for row_id, text, photo, audio in db.execute(
            f"SELECT id, text, photo, audio FROM {table}"
        ).fetchall():
            removed = [w for w, v in (("фото", photo), ("голосовое сообщение", audio))
                       if v and v != "null"]
            if removed:
                note = broadcast_note(removed)
                db.execute(
                    f"UPDATE {table} SET text = ?, photo = NULL, audio = NULL WHERE id = ?",
                    ((text or "") + note, row_id),
                )

    # Следы реальных чатов и школы
    db.execute("DELETE FROM bot_messages")
    db.execute("DELETE FROM app_settings WHERE key IN ('channel_id', 'channel_title')")
    # В демо модератор проверяет ответы: видны все текущие и архивные проверки
    db.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('moderator_review', '1')")
    db.execute("UPDATE quests SET channel_post = 0")

    # Фамилии реальных людей в свободном тексте
    pattern = re.compile("|".join(map(re.escape, sorted(real_stems, key=len, reverse=True))), re.I)
    replaced = 0
    for table, column, fill in (
        ("rewards", "description", HIDDEN_TEXT),
        ("quests", "description", HIDDEN_TEXT),
        ("quests", "approval_note", None),
        ("broadcasts", "text", None),
        ("broadcast_drafts", "text", None),
        ("coin_transactions", "reason", HIDDEN_REASON),
        ("submissions", "review_comment", HIDDEN_COMMENT),
    ):
        for row_id, value in db.execute(f"SELECT rowid, {column} FROM {table}").fetchall():
            if value and real_stems and pattern.search(value):
                if fill is None and table.startswith("broadcast"):
                    db.execute(f"DELETE FROM {table} WHERE rowid = ?", (row_id,))
                    if table == "broadcasts":
                        db.execute("DELETE FROM broadcast_recipients WHERE broadcast_id = ?", (row_id,))
                else:
                    db.execute(f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (fill, row_id))
                replaced += 1
    stats["texts_replaced"] = replaced

    # Ответы на проверке
    added = 0
    students = [sid for (sid,) in db.execute(
        "SELECT id FROM users WHERE role = 'student' AND class_id IS NOT NULL ORDER BY id"
    )]
    now = datetime.now(timezone.utc)
    for i, (title, text) in enumerate(PENDING):
        if added == PENDING_COUNT:
            break
        quest = db.execute(
            "SELECT id FROM quests WHERE title = ? AND is_active AND kind = 'photo'", (title,)
        ).fetchone()
        if quest and students:
            db.execute(
                "INSERT INTO submissions (quest_id, user_id, status, text, created_at) "
                "VALUES (?, ?, 'pending', ?, ?)",
                (quest[0], students[(i * 7) % len(students)], DEMO_EXAMPLE + text,
                 (now - timedelta(hours=i + 1)).strftime("%Y-%m-%d %H:%M:%S.%f")),
            )
            added += 1
    stats["pending_added"] = added
    db.commit()

    # Проверка: ни одной реальной фамилии в текстовых колонках
    leaks = []
    if real_stems:
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
            cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})") if r[2].upper().startswith(("VARCHAR", "TEXT", "JSON"))]
            for col in cols:
                for (value,) in db.execute(f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL"):
                    if isinstance(value, str) and pattern.search(value):
                        leaks.append(f"{table}.{col}: {value[:60]}")
    stats["leaks"] = len(leaks)
    for leak in leaks[:20]:
        print("  остался след:", leak)

    Path(dst).unlink(missing_ok=True)
    db.execute("VACUUM INTO ?", (dst,))
    db.close()
    return stats


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    result = anonymize(sys.argv[1], sys.argv[2])
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(1 if result["leaks"] else 0)
