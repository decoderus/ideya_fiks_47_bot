import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    admin = "admin"
    # Помощник админа: почти тот же интерфейс, но без управления монетами,
    # экономикой и ролями; оповещения — только через подтверждение админа
    moderator = "moderator"
    teacher = "teacher"
    student = "student"


class QuestKind(str, enum.Enum):
    auto = "auto"  # ученик сам отмечает «выполнено», монеты сразу
    # Ученик присылает ответ (текст, фото или видео, можно с подписью), учитель подтверждает.
    # Значение «photo» осталось с тех пор, когда ответом могло быть только фото
    photo = "photo"
    # Вопрос дня: каждому ученику свой вопрос из банка app/quiz_bank.py, бот проверяет сам
    quiz = "quiz"


class QuestRepeat(str, enum.Enum):
    """Как часто ученик может снова выполнить квест (календарные периоды по Москве)."""

    none = "none"  # один раз
    daily = "daily"  # раз в день, с полуночи
    weekly = "weekly"  # раз в неделю, с понедельника
    monthly = "monthly"  # раз в месяц, с 1-го числа


class SubmissionStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class SchoolClass(Base):
    __tablename__ = "classes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)  # например «10А»


# Какие классы ведёт учитель (у ученика класс один — users.class_id)
teacher_classes = Table(
    "teacher_classes",
    Base.metadata,
    Column("teacher_id", ForeignKey("users.id"), primary_key=True),
    Column("class_id", ForeignKey("classes.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    max_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")  # имя из профиля MAX
    # None — регистрация не завершена (или учитель ждёт подтверждения заявки)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.student)
    # Класс ученика. У учителей и админов не используется
    class_id: Mapped[int | None] = mapped_column(
        ForeignKey("classes.id"), nullable=True
    )
    balance: Mapped[int] = mapped_column(Integer, default=0)
    # Ученик отключил утренние напоминания о сериях
    reminders_off: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # Открывал ли «ℹ️ Помощь»: до первого просмотра кнопка — первая в меню, потом — последняя
    help_seen: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # Текущий шаг диалога (ввод ФИО, создание квеста, ожидание фото) и его данные
    state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    state_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    school_class: Mapped[SchoolClass | None] = relationship(lazy="joined")
    teacher_classes: Mapped[list[SchoolClass]] = relationship(
        secondary=teacher_classes, lazy="selectin", order_by=SchoolClass.name
    )

    @property
    def teacher_class_ids(self) -> set[int]:
        return {c.id for c in self.teacher_classes}

    @property
    def display_name(self) -> str:
        return self.full_name or self.name or str(self.max_user_id)


class Quest(Base):
    __tablename__ = "quests"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    reward: Mapped[int] = mapped_column(Integer)  # награда (при диапазоне — максимум)
    # Нижняя граница диапазона: точную сумму выбирает проверяющий. None — награда фиксированная
    reward_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Назначенные проверяющие-учителя (id). Если заданы — из учителей проверяют только они
    reviewer_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    kind: Mapped[QuestKind] = mapped_column(Enum(QuestKind))
    # None = квест для всех классов
    class_id: Mapped[int | None] = mapped_column(
        ForeignKey("classes.id"), nullable=True
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    photo: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # картинка квеста (payload MAX)
    repeat: Mapped[QuestRepeat] = mapped_column(
        Enum(QuestRepeat), default=QuestRepeat.none, server_default=QuestRepeat.none.value
    )
    # Квест модератора до публикации: "pending" — ждёт админа, "rework" — на доработке.
    # None — опубликован (или не требовал подтверждения)
    approval: Mapped[str | None] = mapped_column(String(16), nullable=True)
    approval_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # комментарий админа
    # Засчитанные ответы проверяющий может опубликовать в канале школы
    channel_post: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    author: Mapped[User] = relationship(foreign_keys=[author_id], lazy="joined")


class Submission(Base):
    """Выполнение квеста учеником. Для авто-квестов сразу approved."""

    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    quest_id: Mapped[int] = mapped_column(ForeignKey("quests.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[SubmissionStatus] = mapped_column(Enum(SubmissionStatus))
    photo: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # payload вложения MAX
    text: Mapped[str | None] = mapped_column(Text, nullable=True)  # текст ответа ученика
    video: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # payload видео MAX
    # Сколько монет начислено при засчитывании (награда квеста могла потом измениться)
    reward: Mapped[int | None] = mapped_column(Integer, nullable=True)
    question_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # номер в quiz_bank
    # Комментарий проверяющего: причина отклонения (её видит ученик)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    # Публикация в канале школы (квесты с channel_post): None — не предлагалась,
    # offered — ждёт решения проверяющего, rework — у автора на доработке,
    # published — опубликовано, skipped — решили не публиковать
    publication: Mapped[str | None] = mapped_column(String(16), nullable=True)
    publication_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # что доработать
    # Исправленный автором вариант для публикации: {"text", "photo", "video"}
    publication_draft: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    quest: Mapped[Quest] = relationship(lazy="joined")
    user: Mapped[User] = relationship(foreign_keys=[user_id], lazy="joined")
    reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewer_id], lazy="joined")

    @property
    def earned(self) -> int:
        """Начисленные за ответ монеты (для старых записей — награда квеста)."""
        return self.reward if self.reward is not None else self.quest.reward


class BotMessage(Base):
    """Сообщения, отправленные ботом: их удаляет кнопка «Перезапустить»."""

    __tablename__ = "bot_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    max_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    mid: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Broadcast(Base):
    """Оповещение, разосланное админом школе или классу."""

    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text, default="")
    photo: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # payload вложения MAX
    audio: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # голосовое или аудио
    # None = вся школа
    class_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id"), nullable=True)
    # Кому: см. services/broadcasts.py; None — старое оповещение (вся школа или class_id)
    audience: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recipients: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    author: Mapped[User] = relationship(lazy="joined")
    school_class: Mapped[SchoolClass | None] = relationship(lazy="joined")


class BroadcastRecipient(Base):
    """Кому ушло оповещение и прочитал ли он его."""

    __tablename__ = "broadcast_recipients"

    id: Mapped[int] = mapped_column(primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(ForeignKey("broadcasts.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    broadcast: Mapped[Broadcast] = relationship(lazy="joined")


class OrderStatus(str, enum.Enum):
    pending = "pending"  # куплено, ждёт выдачи
    issued = "issued"  # выдано
    cancelled = "cancelled"  # отменено, монеты возвращены


class RewardKind(str, enum.Enum):
    item = "item"  # обычная награда: покупка ждёт выдачи
    lottery = "lottery"  # колесо фортуны: с шансом win_chance % начисляет prize монет


class Reward(Base):
    """Награда в магазине."""

    __tablename__ = "rewards"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[int] = mapped_column(Integer)
    photo: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # payload вложения MAX
    stock: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = без ограничения
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    kind: Mapped[RewardKind] = mapped_column(
        Enum(RewardKind), default=RewardKind.item, server_default=RewardKind.item.value
    )
    win_chance: Mapped[int | None] = mapped_column(Integer, nullable=True)  # в процентах
    prize: Mapped[int | None] = mapped_column(Integer, nullable=True)  # монет при выигрыше
    # Критический провал колеса: шанс в процентах и сколько монет забирает (не ниже нуля)
    loss_chance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loss: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Order(Base):
    """Покупка награды учеником."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    reward_id: Mapped[int] = mapped_column(ForeignKey("rewards.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    price: Mapped[int] = mapped_column(Integer)  # цена на момент покупки
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.pending)
    handler_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Для колеса фортуны: сколько монет выиграно (0 — не повезло, меньше нуля — провал)
    prize_won: Mapped[int | None] = mapped_column(Integer, nullable=True)

    reward: Mapped[Reward] = relationship(lazy="joined")
    user: Mapped[User] = relationship(foreign_keys=[user_id], lazy="joined")


class CoinTransaction(Base):
    """Ручное начисление (amount > 0) или списание (amount < 0) монет учителем или админом."""

    __tablename__ = "coin_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)  # ученик
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)  # кто изменил
    amount: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    actor: Mapped[User] = relationship(foreign_keys=[actor_id], lazy="joined")


class TeacherRequestStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    withdrawn = "withdrawn"  # отозвана самим пользователем


class TeacherRequest(Base):
    """Заявка на роль учителя: учитель выбирает классы, админ или модератор подтверждает."""

    __tablename__ = "teacher_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(128))
    # Классы, в которых учитель работает (по его выбору; проверяющий может изменить)
    class_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[TeacherRequestStatus] = mapped_column(
        Enum(TeacherRequestStatus), default=TeacherRequestStatus.pending
    )
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(foreign_keys=[user_id], lazy="joined")


class EnrollmentLog(Base):
    """Журнал зачислений учеников при регистрации: админ просматривает его в «Регистрациях»."""

    __tablename__ = "enrollment_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id"), nullable=True)
    # Как было на момент регистрации: ученика потом могут переименовать, перевести или удалить
    full_name: Mapped[str] = mapped_column(String(128))
    class_name: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AchievementKind(str, enum.Enum):
    manual = "manual"  # выдаёт админ
    streak = "streak"  # ежедневный квест threshold дней подряд (quest_id)
    quests_count = "quests_count"  # выполнено threshold квестов
    coins_earned = "coins_earned"  # заработано threshold монет за всё время
    first_purchase = "first_purchase"  # первая покупка в магазине


class Achievement(Base):
    """Достижение: выдаётся админом (manual) или автоматически по условию."""

    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    emoji: Mapped[str] = mapped_column(String(16), default="🏅")
    kind: Mapped[AchievementKind] = mapped_column(Enum(AchievementKind))
    threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quest_id: Mapped[int | None] = mapped_column(ForeignKey("quests.id"), nullable=True)
    reward: Mapped[int] = mapped_column(Integer, default=0)  # бонус монетами при получении
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    quest: Mapped[Quest | None] = relationship(lazy="joined")


class UserAchievement(Base):
    """Полученное учеником достижение."""

    __tablename__ = "user_achievements"
    __table_args__ = (UniqueConstraint("achievement_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    achievement_id: Mapped[int] = mapped_column(ForeignKey("achievements.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # None — авто
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    achievement: Mapped[Achievement] = relationship(lazy="joined")


class ContestPeriod(str, enum.Enum):
    week = "week"
    month = "month"


class ContestCriterion(str, enum.Enum):
    total = "total"  # больше всех монет за период
    average = "average"  # больше всех монет на одного ученика класса


class ClassContestResult(Base):
    """Итоги соревнования классов: призовые места за неделю или месяц."""

    __tablename__ = "class_contest_results"
    __table_args__ = (UniqueConstraint("period", "period_start", "criterion", "place"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    period: Mapped[ContestPeriod] = mapped_column(Enum(ContestPeriod))
    period_start: Mapped[date] = mapped_column(Date)  # первый день периода по Москве
    criterion: Mapped[ContestCriterion] = mapped_column(Enum(ContestCriterion))
    place: Mapped[int] = mapped_column(Integer)  # 0 — отметка «итоги подведены, призёров нет»
    class_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id"), nullable=True)
    class_name: Mapped[str] = mapped_column(String(32))  # на момент подведения итогов
    value: Mapped[int] = mapped_column(Integer)  # сумма или среднее (округлено)
    active: Mapped[int] = mapped_column(Integer)  # активных участников
    students: Mapped[int] = mapped_column(Integer)  # учеников в классе
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Season(Base):
    """Сезон рейтинга (четверть): от start_date до начала следующего сезона."""

    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(64))  # «I четверть 2026/27»
    start_date: Mapped[date] = mapped_column(Date, unique=True)  # по Москве
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SeasonResult(Base):
    """Итоги сезона: топ школы (class_id=None) и лучшие ученики каждого класса."""

    __tablename__ = "season_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), index=True)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id"), nullable=True)
    class_name: Mapped[str] = mapped_column(String(32), default="")
    place: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    full_name: Mapped[str] = mapped_column(String(128))  # на момент подведения итогов
    points: Mapped[int] = mapped_column(Integer)


class DraftStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    rejected = "rejected"


class BroadcastDraft(Base):
    """Оповещение модератора: уходит только после подтверждения админом."""

    __tablename__ = "broadcast_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text, default="")
    photo: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    audio: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id"), nullable=True)
    audience: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[DraftStatus] = mapped_column(Enum(DraftStatus), default=DraftStatus.pending)
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    broadcast_id: Mapped[int | None] = mapped_column(ForeignKey("broadcasts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    author: Mapped[User] = relationship(foreign_keys=[author_id], lazy="joined")
    school_class: Mapped[SchoolClass | None] = relationship(lazy="joined")


class AppSetting(Base):
    """Настройки школы, которые админ меняет из бота (ключ — значение)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256))


class QuestQuestion(Base):
    """Вопрос ученика: по квесту (получают админы, модераторы и учитель — автор квеста)
    или общий, без квеста (получают админы и модераторы)."""

    __tablename__ = "quest_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    quest_id: Mapped[int | None] = mapped_column(ForeignKey("quests.id"), nullable=True, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    quest: Mapped[Quest | None] = relationship(lazy="joined")
    student: Mapped[User] = relationship(lazy="joined")
    answers: Mapped[list["QuestAnswer"]] = relationship(
        lazy="selectin", order_by="QuestAnswer.id", back_populates="question"
    )


class QuestAnswer(Base):
    """Ответ на вопрос ученика. Роль автора сохраняется на момент ответа."""

    __tablename__ = "quest_answers"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("quest_questions.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    author_role: Mapped[Role] = mapped_column(Enum(Role))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    question: Mapped[QuestQuestion] = relationship(back_populates="answers")
    author: Mapped[User] = relationship(lazy="joined")


class Success(Base):
    """Запись об успехе ученика в «Журнале успехов»: делает учитель или админ.
    Роль автора сохраняется на момент записи; coins — сколько монет начислено вместе с ней."""

    __tablename__ = "successes"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    author_role: Mapped[Role] = mapped_column(Enum(Role))
    category: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    coins: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    student: Mapped[User] = relationship(foreign_keys=[student_id], lazy="joined")
    author: Mapped[User] = relationship(foreign_keys=[author_id], lazy="joined")
