"""Промо-ролик для учителей «Сферум. Актив»: 28 секунд, 1080×1920, без звука.

Кадры рисуются Pillow и передаются в ffmpeg (из imageio-ffmpeg).
python make_promo.py [--preview]  — с --preview сохраняет несколько кадров PNG вместо видео.
"""
import math
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1080, 1920, 30
DURATION = 29.5  # уточняется по LENGTHS ниже
OUT = Path(__file__).with_name("teacher_promo.mp4")
FONTS = Path("C:/Windows/Fonts")

INK = (36, 44, 62)
MUTED = (96, 106, 126)
CREAM_TOP = (255, 244, 228)
CREAM_BOTTOM = (255, 224, 192)
GOLD = (232, 176, 52)
GOLD_DARK = (190, 128, 20)
TEAL = (38, 150, 136)
RED = (226, 104, 78)
BLUE = (58, 112, 176)
WHITE = (255, 255, 255)
CHAT = (233, 238, 245)
ME = (214, 232, 255)


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


BLACK = "seguibl.ttf"
BOLD = "segoeuib.ttf"
SEMI = "seguisb.ttf"
REG = "segoeui.ttf"


# --- анимация ---


def clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def ease(x: float) -> float:
    x = clamp(x)
    return 1 - (1 - x) ** 3


def appear(t: float, start: float, dur: float = 0.45) -> float:
    return ease((t - start) / dur)


# --- рисование элементов (в 2×, затем уменьшение — сглаженные края) ---

S = 2


def element(w: int, h: int, draw_fn) -> Image.Image:
    img = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(img), img)
    return img.resize((w, h), Image.LANCZOS)


def rr(d, box, fill, radius, outline=None, width=0):
    d.rounded_rectangle([v * S for v in box], radius=radius * S, fill=fill, outline=outline, width=width * S)


def txt(d, xy, text, f, fill, anchor="la", spacing=10):
    big = ImageFont.truetype(f.path, f.size * S)
    align = "center" if anchor[0] == "m" else ("right" if anchor[0] == "r" else "left")
    d.multiline_text(
        (xy[0] * S, xy[1] * S), text, font=big, fill=fill, anchor=anchor, spacing=spacing * S, align=align
    )


def check(d, cx, cy, size, fill, width=7):
    pts = [(cx - size * 0.45, cy), (cx - size * 0.1, cy + size * 0.35), (cx + size * 0.5, cy - size * 0.35)]
    d.line([(x * S, y * S) for x, y in pts], fill=fill, width=width * S, joint="curve")


def coin(size: int) -> Image.Image:
    def draw(d, img):
        c = size / 2
        d.ellipse([0, 0, size * S, size * S], fill=GOLD_DARK)
        d.ellipse([size * 0.06 * S, size * 0.04 * S, size * 0.94 * S, size * 0.9 * S], fill=GOLD)
        d.ellipse([size * 0.18 * S, size * 0.15 * S, size * 0.82 * S, size * 0.79 * S], outline=(255, 222, 140), width=int(size * 0.04 * S))
        pts = []
        for i in range(10):
            a = math.pi / 2 + i * math.pi / 5
            r = size * (0.2 if i % 2 == 0 else 0.085)
            pts.append(((c + r * math.cos(a)) * S, (c * 0.94 - r * math.sin(a)) * S))
        d.polygon(pts, fill=WHITE)
    return element(size, size, draw)


def bubble(text: str, me: bool, width: int = 560) -> Image.Image:
    f = font(SEMI if me else REG, 40)
    lines = text.split("\n")
    h = 46 * len(lines) + 44
    def draw(d, img):
        rr(d, (0, 0, width, h), ME if me else WHITE, 28)
        txt(d, (30, 20), text, f, INK, spacing=6)
    return element(width, h, draw)


def card(title: str, body: str, accent, chip: str | None = None, width: int = 600) -> Image.Image:
    fb, ft = font(BOLD, 42), font(REG, 36)
    body_h = 44 * len(body.split("\n")) if body else 0
    h = 96 + body_h + (78 if chip else 0) + 26
    def draw(d, img):
        rr(d, (0, 0, width, h), WHITE, 30)
        rr(d, (0, 0, width, 70), accent, 30)
        d.rectangle([0, 40 * S, width * S, 70 * S], fill=accent)
        txt(d, (30, 12), title, font(BOLD, 36), WHITE)
        y = 90
        if body:
            txt(d, (30, y), body, ft, INK, spacing=6)
            y += body_h + 10
        if chip:
            rr(d, (30, y, 30 + 30 + int(fb.getlength(chip)), y + 60), (255, 238, 196), 30)
            txt(d, (45, y + 6), chip, fb, GOLD_DARK)
    return element(width, h, draw)


def button(text: str, fill, color=WHITE, width: int = 280, h: int = 86) -> Image.Image:
    f = font(BOLD, 38)
    def draw(d, img):
        rr(d, (0, 0, width, h), fill, 24)
        txt(d, (width / 2, h / 2), text, f, color, anchor="mm")
    return element(width, h, draw)


def headline(text: str, step: str | None) -> Image.Image:
    f = font(BLACK, 76)
    lines = text.split("\n")
    h = 92 * len(lines) + (90 if step else 0)
    def draw(d, img):
        y = 0
        if step:
            fs = font(BOLD, 34)
            w = int(fs.getlength(step)) + 56
            rr(d, (0, 0, w, 60), INK, 30)
            txt(d, (w / 2, 30), step, fs, WHITE, anchor="mm")
            y = 90
        txt(d, (0, y), text, f, INK, spacing=14)
    return element(W - 160, h, draw)


def desk_photo(w: int = 540, h: int = 300) -> Image.Image:
    def draw(d, img):
        rr(d, (0, 0, w, h), (207, 226, 240), 22)
        rr(d, (40, 170, w - 40, 200), (170, 120, 80), 8)                 # стол
        rr(d, (70, 200, 92, h - 10), (140, 95, 62), 4)
        rr(d, (w - 92, 200, w - 70, h - 10), (140, 95, 62), 4)
        rr(d, (90, 110, 210, 170), BLUE, 8)                               # книги
        rr(d, (100, 80, 200, 110), RED, 8)
        rr(d, (250, 120, 360, 170), WHITE, 6)                             # тетрадь
        d.ellipse([400 * S, 110 * S, 460 * S, 170 * S], fill=TEAL)        # кружка-карандашница
        for x in (415, 430, 445):
            d.line([(x * S, 110 * S), (x * S, 70 * S)], fill=GOLD_DARK, width=6 * S)
    return element(w, h, draw)


# --- фон и телефон (кэшируются) ---


@lru_cache
def background() -> Image.Image:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(CREAM_TOP, CREAM_BOTTOM)))
    # мягкие круги-«монетки» на фоне
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    for cx, cy, r in ((120, 260, 140), (980, 520, 110), (90, 1500, 120), (1010, 1750, 150)):
        g.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 210, 130, 70))
    glow = glow.filter(ImageFilter.GaussianBlur(40))
    return Image.alpha_composite(img.convert("RGBA"), glow)


PHONE = (170, 560, 910, 1820)  # рамка телефона
SCREEN = (196, 600, 884, 1790)


@lru_cache
def phone() -> Image.Image:
    x0, y0, x1, y1 = PHONE
    w, h = x1 - x0, y1 - y0
    def draw(d, img):
        rr(d, (0, 0, w, h), INK, 70)
        sx0, sy0, sx1, sy1 = (v - o for v, o in zip(SCREEN, (x0, y0, x0, y0)))
        rr(d, (sx0, sy0, sx1, sy1), CHAT, 46)
        rr(d, (w / 2 - 70, 12, w / 2 + 70, 30), (60, 70, 90), 9)             # динамик
        # шапка чата
        rr(d, (sx0, sy0, sx1, sy0 + 110), WHITE, 46)
        d.rectangle([sx0 * S, (sy0 + 60) * S, sx1 * S, (sy0 + 110) * S], fill=WHITE)
        d.ellipse([(sx0 + 28) * S, (sy0 + 24) * S, (sx0 + 90) * S, (sy0 + 86) * S], fill=GOLD)
        txt(d, (sx0 + 108, sy0 + 24), "Сферум. Актив", font(BOLD, 36), INK)
        txt(d, (sx0 + 108, sy0 + 64), "бот", font(REG, 26), MUTED)
    shadow = Image.new("RGBA", (w + 120, h + 120), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([60, 80, w + 60, h + 60], radius=70, fill=(80, 50, 20, 90))
    shadow = shadow.filter(ImageFilter.GaussianBlur(30))
    body = element(w, h, draw)
    out = Image.new("RGBA", shadow.size, (0, 0, 0, 0))
    out.alpha_composite(shadow)
    out.alpha_composite(body, (60, 60))
    return out


def put(frame: Image.Image, el: Image.Image, x: float, y: float, alpha: float = 1.0, scale: float = 1.0):
    if alpha <= 0:
        return
    if scale != 1.0:
        el = el.resize((max(1, int(el.width * scale)), max(1, int(el.height * scale))), Image.LANCZOS)
        x += (el.width / scale - el.width) / 2
        y += (el.height / scale - el.height) / 2
    if alpha < 1:
        el = el.copy()
        el.putalpha(el.getchannel("A").point(lambda a: int(a * alpha)))
    frame.alpha_composite(el, (int(x), int(y)))


def with_phone(frame: Image.Image, t: float, start_alpha: float = 1.0):
    put(frame, phone(), PHONE[0] - 60, PHONE[1] - 60, start_alpha)


# --- сцены (t — секунды от начала сцены) ---

CHAT_X, CHAT_X_ME = SCREEN[0] + 26, SCREEN[2] - 26


def scene_title(f: Image.Image, t: float):
    a = appear(t, 0.0, 0.6)
    put(f, coin(300), (W - 300) / 2, 470 - 60 * (1 - a), a, 0.6 + 0.4 * a)
    title = element(W - 120, 150, lambda d, i: txt(d, ((W - 120) / 2, 70), "Сферум. Актив", font(BLACK, 110), INK, anchor="mm"))
    put(f, title, 60, 860 + 40 * (1 - appear(t, 0.4)), appear(t, 0.4))
    sub = element(W - 120, 160, lambda d, i: txt(d, ((W - 120) / 2, 70), "Школьный бот\nв мессенджере MAX", font(SEMI, 60), MUTED, anchor="mm", spacing=12))
    put(f, sub, 60, 1030, appear(t, 0.9))
    tags = element(W - 120, 120, lambda d, i: txt(d, ((W - 120) / 2, 60), "квесты · монеты · награды", font(BOLD, 54), GOLD_DARK, anchor="mm"))
    put(f, tags, 60, 1240, appear(t, 1.5))


def scene_create(f: Image.Image, t: float):
    put(f, headline("Даёте задание\nза минуту", "Шаг 1"), 80, 150, appear(t, 0.0))
    with_phone(f, t, appear(t, 0.1))
    msgs = [
        (0.7, bubble("Название квеста?", False)),
        (1.3, bubble("Порядок на столе", True, 460)),
        (1.9, bubble("Награда в монетах?", False)),
        (2.5, bubble("150", True, 180)),
        (3.2, card("Квест создан", "Уведомление получили\n25 учеников 7А", TEAL)),
    ]
    y = SCREEN[1] + 140
    for start, el in msgs:
        a = appear(t, start, 0.35)
        me = el.width < 560 and start in (1.3, 2.5)
        x = CHAT_X_ME - el.width if me else CHAT_X
        put(f, el, x, y + 30 * (1 - a), a)
        y += el.height + 22


def scene_review(f: Image.Image, t: float):
    put(f, headline("Ученик шлёт фото —\nвы засчитываете\nодной кнопкой", "Шаг 2"), 80, 150, appear(t, 0.0))
    with_phone(f, t)
    y = SCREEN[1] + 140
    head = card("Новый ответ", "Маша К., 7А\n«Порядок на столе»", BLUE, width=636)
    put(f, head, CHAT_X, y + 30 * (1 - appear(t, 0.4)), appear(t, 0.4))
    y += head.height + 20
    photo = desk_photo(636, 330)
    put(f, photo, CHAT_X, y, appear(t, 0.9))
    y += photo.height + 24
    tapped = t >= 3.0
    ok = button("Засчитано" if tapped else "Засчитать", TEAL, width=306)
    no = button("Отклонить", WHITE, INK, width=306)
    a = appear(t, 1.5)
    put(f, ok, CHAT_X, y, a, 0.94 if 2.85 <= t < 3.05 else 1.0)
    put(f, no, CHAT_X + 330, y, a * (0.4 if tapped else 1.0))
    if 2.7 <= t < 3.4:  # касание пальцем
        p = clamp((t - 2.7) / 0.7)
        r = int(30 + 70 * p)
        ring = Image.new("RGBA", (2 * r + 10, 2 * r + 10), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse([5, 5, 2 * r + 5, 2 * r + 5], outline=(255, 255, 255, int(230 * (1 - p))), width=8)
        f.alpha_composite(ring, (int(CHAT_X + 153 - r - 5), int(y + 43 - r - 5)))
    if tapped:
        plus = element(560, 120, lambda d, i: (rr(d, (0, 0, 560, 120), GOLD, 60), txt(d, (280, 60), "+150 монет ученику", font(BLACK, 48), WHITE, anchor="mm")))
        a2 = appear(t, 3.1, 0.35)
        put(f, plus, (W - 560) / 2, y + 130 - 40 * a2, a2)


def scene_shop(f: Image.Image, t: float):
    put(f, headline("Монеты — на награды,\nклассы — в рейтинг", "Шаг 3"), 80, 150, appear(t, 0.0))
    with_phone(f, t)
    y = SCREEN[1] + 140
    rows = [("Своё место в классе", "1 500"), ("Диджей перемены", "3 000"), ("Выбор классного часа", "4 000")]
    shop = card("Магазин наград", "", RED, width=636)
    put(f, shop, CHAT_X, y, appear(t, 0.4))
    y += 96
    for i, (name, price) in enumerate(rows):
        row = element(636, 84, lambda d, im, n=name, p=price: (
            rr(d, (0, 0, 636, 76), WHITE, 18),
            txt(d, (26, 38), n, font(SEMI, 36), INK, anchor="lm"),
            txt(d, (610, 38), p, font(BOLD, 36), GOLD_DARK, anchor="rm"),
        ))
        a = appear(t, 0.8 + 0.35 * i)
        put(f, row, CHAT_X + 40 * (1 - a), y, a)
        y += 88
    y += 30
    rating = element(636, 330, lambda d, im: (
        rr(d, (0, 0, 636, 330), WHITE, 30),
        rr(d, (0, 0, 636, 70), GOLD_DARK, 30),
        d.rectangle([0, 40 * S, 636 * S, 70 * S], fill=GOLD_DARK),
        txt(d, (30, 12), "Класс недели", font(BOLD, 36), WHITE),
        txt(d, (30, 100), "1   7А — 2 400 монет", font(BOLD, 42), INK),
        txt(d, (30, 168), "2   8Б — 1 950", font(SEMI, 38), MUTED),
        txt(d, (30, 228), "3   5А — 1 200", font(SEMI, 38), MUTED),
    ))
    a = appear(t, 2.2)
    put(f, rating, CHAT_X, y + 40 * (1 - a), a)


def scene_benefits(f: Image.Image, t: float):
    put(f, headline("Учителю —\nпросто", None), 80, 250, appear(t, 0.0))
    items = [
        ("Задание — за минуту", "без бумаг и таблиц"),
        ("Бот сам всё рассылает", "уведомления и напоминания"),
        ("Только ваши классы", "понятные лимиты и журнал"),
    ]
    y = 640
    for i, (head, sub) in enumerate(items):
        def draw(d, im, h=head, s=sub):
            rr(d, (0, 0, W - 160, 230), WHITE, 36)
            d.ellipse([40 * S, 55 * S, 160 * S, 175 * S], fill=TEAL)
            check(d, 100, 113, 60, WHITE, 10)
            txt(d, (200, 52), h, font(BOLD, 48), INK)
            txt(d, (200, 124), s, font(REG, 38), MUTED)
        el = element(W - 160, 230, draw)
        a = appear(t, 0.5 + 0.5 * i)
        put(f, el, 80 + 60 * (1 - a), y, a)
        y += 270


def scene_cta(f: Image.Image, t: float):
    a = appear(t, 0.0, 0.5)
    put(f, coin(220), (W - 220) / 2, 230, a, 0.7 + 0.3 * a)
    head = element(W - 120, 230, lambda d, i: txt(d, ((W - 120) / 2, 110), "Попробуйте\nуже сегодня", font(BLACK, 96), INK, anchor="mm", spacing=14))
    put(f, head, 60, 500, appear(t, 0.3))
    steps = [("1", "Откройте MAX"), ("2", "Найдите бота\n@demo_active_bot"), ("3", "Выберите «Учитель»\nи отправьте заявку")]
    y = 820
    for i, (n, text) in enumerate(steps):
        lines = text.count("\n") + 1
        h = 70 + 58 * lines
        def draw(d, im, n=n, text=text, h=h):
            rr(d, (0, 0, W - 160, h), WHITE, 32)
            d.ellipse([34 * S, (h / 2 - 44) * S, 122 * S, (h / 2 + 44) * S], fill=GOLD)
            txt(d, (78, h / 2), n, font(BLACK, 50), WHITE, anchor="mm")
            txt(d, (160, h / 2), text, font(BOLD, 48), INK, anchor="lm", spacing=8)
        el = element(W - 160, h, draw)
        a2 = appear(t, 0.7 + 0.4 * i)
        put(f, el, 80, y + 30 * (1 - a2), a2)
        y += h + 30
    foot = element(W - 120, 90, lambda d, i: txt(d, ((W - 120) / 2, 45), "Сферум. Актив — школа, в которой интересно", font(SEMI, 40), MUTED, anchor="mm"))
    put(f, foot, 60, 1700, appear(t, 2.0))


# Длительности сцен подобраны под фразы озвучки (voice/trim_*.wav)
LENGTHS = [3.6, 5.3, 5.3, 5.5, 4.9, 4.9]
FUNCS = [scene_title, scene_create, scene_review, scene_shop, scene_benefits, scene_cta]
SCENES = []
_t = 0.0
for _len, _fn in zip(LENGTHS, FUNCS):
    SCENES.append((_t, _t + _len, _fn))
    _t += _len
DURATION = _t
FADE = 0.35
VOICE_DIR = Path(__file__).with_name("voice")
VOICE_LEAD = 0.2  # фраза начинается чуть позже смены кадра


def narration() -> Path | None:
    """Склеить фразы озвучки по началам сцен в одну дорожку WAV."""
    import array
    import wave

    files = sorted(VOICE_DIR.glob("trim_*.wav"))
    if len(files) != len(SCENES):
        return None
    rate = wave.open(str(files[0])).getframerate()
    track = array.array("h", bytes(2 * int(DURATION * rate)))
    for (start, _, _), path in zip(SCENES, files):
        w = wave.open(str(path))
        data = array.array("h", w.readframes(w.getnframes()))
        w.close()
        at = int((start + VOICE_LEAD) * rate)
        track[at : at + len(data)] = data[: len(track) - at]
    out = Path(__file__).with_name("narration.wav")
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(track.tobytes())
    return out


def render(t: float) -> Image.Image:
    frame = background().copy()
    for start, end, fn in SCENES:
        if start - FADE <= t < end:
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            fn(layer, t - start)
            alpha = 1.0
            if t < start:
                alpha = 0.0
            elif end < DURATION and t > end - FADE:
                alpha = clamp((end - t) / FADE)
            if alpha > 0:
                if alpha < 1:
                    layer.putalpha(layer.getchannel("A").point(lambda a, k=alpha: int(a * k)))
                frame.alpha_composite(layer)
    return frame.convert("RGB")


def main() -> None:
    if "--preview" in sys.argv:
        for t in (1.8, 7.5, 12.6, 18.5, 22.5, 27.0):
            render(t).save(Path(__file__).with_name(f"preview_{t:04.1f}.png"))
        print("превью сохранены")
        return
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    # Звук: фоновая музыка (music.wav из make_music.py), иначе озвучка, иначе без звука
    music = Path(__file__).with_name("music.wav")
    voice = music if music.exists() else narration()
    audio_in = ["-i", str(voice)] if voice else []
    audio_out = ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "160k"] if voice else []
    proc = subprocess.Popen(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", *audio_in,
         *audio_out,
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", "-shortest", str(OUT)],
        stdin=subprocess.PIPE,
    )
    print("звук:", voice or "нет — ролик без звука")
    frames = int(DURATION * FPS)
    for i in range(frames):
        proc.stdin.write(render(i / FPS).tobytes())
        if i % 150 == 0:
            print(f"кадр {i}/{frames}", flush=True)
    proc.stdin.close()
    proc.wait()
    print("готово:", OUT, f"{OUT.stat().st_size / 1e6:.1f} МБ")


if __name__ == "__main__":
    main()
