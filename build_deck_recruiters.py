# -*- coding: utf-8 -*-
"""Презентация «Агент подбора персонала» для службы подбора -> PDF.

Запуск:  set PYTHONIOENCODING=utf-8 && python build_presentation.py

Сборка сама себя проверяет и падает, а не выпускает файл с дефектом:
  - текст помещается в свой блок (перенос и высота считаются по метрикам шрифта);
  - текст внутри карточки не выходит за её края;
  - блоки не выходят за рабочую область слайда и не налезают друг на друга;
  - стрелки не проходят сквозь блоки и доходят до краёв своих блоков.
Этим закрывается геометрия. Вёрстку глазами — отдельно, по одному слайду
в полном размере: миниатюры всего документа мелкие дефекты не показывают.

Материал для внешнего читателя: без имён сотрудников, цифр финмодели,
порогов вознаграждения и данных о закупках третьих лиц.
"""
import math
import sys

import pymupdf as fitz

OUT = "Презентация_для_рекрутеров.pdf"   # собирается рядом со скриптом
PREVIEW_DIR = None          # путь к папке для PNG-превью; задаётся аргументом
if len(sys.argv) > 1:
    PREVIEW_DIR = sys.argv[1]

W, H = 960, 540
M = 56
CONTENT_BOTTOM = H - 38
VERSION = "Версия 2 · 26 сентября 2026"
DECK = "Агент подбора персонала"

FR = "C:/Windows/Fonts/arial.ttf"
FB = "C:/Windows/Fonts/arialbd.ttf"
FONT_R = fitz.Font(fontfile=FR)
FONT_B = fitz.Font(fontfile=FB)


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


INK, MUTED, LINE, WHITE = rgb("1A1A1A"), rgb("5A5A5A"), rgb("D6D6D6"), (1, 1, 1)
PAL = {
    "blue":   (rgb("E8F1FA"), rgb("2F6FA8")),
    "sand":   (rgb("FCF1E2"), rgb("C07820")),
    "green":  (rgb("E6F4EA"), rgb("1E7B45")),
    "violet": (rgb("F1EAF8"), rgb("6B3FA0")),
    "red":    (rgb("FBEAEA"), rgb("B03030")),
    "grey":   (rgb("F0F0F0"), rgb("5A5A5A")),
    "amber":  (rgb("FFF6DD"), rgb("C98A00")),
    "white":  ((1, 1, 1), rgb("C8C8C8")),
}


# ------------------------------------------------------------------ текст
NBSP = " "
SHORT3 = {"без", "для", "под", "над", "при", "про", "изо", "обо"}


def glue(para):
    """Короткий предлог или союз приклеивается к следующему слову,
    тире и частицы «же», «ли», «бы» — к предыдущему: так ни то ни другое
    не остаётся висеть на краю строки."""
    words = para.split(" ")
    out = []
    i = 0
    while i < len(words):
        w = words[i]
        if (w == "—" or w.strip(".,:;!?»") in ("же", "ли", "бы")) and out:
            out[-1] = out[-1] + NBSP + w
            i += 1
            continue
        tail = w.split(NBSP)[-1].strip("«(")
        short = (len(tail) <= 2 and tail.isalpha()) or tail.lower() in SHORT3
        if short and i + 1 < len(words):
            words[i + 1] = w + NBSP + words[i + 1]
            i += 1
            continue
        out.append(w)
        i += 1
    return out


def wrap(s, size, font, width, where):
    lines = []
    for para in s.split("\n"):
        cur = ""
        for word in glue(para):
            if font.text_length(word, fontsize=size) > width:
                raise SystemExit(f"{where}: слово шире блока ({width:.0f} pt): {word!r}")
            trial = word if not cur else cur + " " + word
            if font.text_length(trial, fontsize=size) <= width:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


class Slide:
    def __init__(self, doc, n, accent="blue"):
        self.p = doc.new_page(width=W, height=H)
        self.n = n
        self.accent = accent
        self.blocks = []      # (имя, "rect"|"circle", геометрия, непрозрачный)
        self.arrows = []      # (точки, откуда, куда)

    # -- текст с проверкой размещения
    def text(self, rect, s, size, bold=False, color=INK, align=0, lh=1.28,
             valign="top", parent=None, register=True):
        r = fitz.Rect(rect)
        font = FONT_B if bold else FONT_R
        where = f"Слайд {self.n}"
        lines = wrap(s, size, font, r.width, where)
        step = size * lh
        need = step * len(lines)
        if need > r.height + 0.01:
            raise SystemExit(f"{where}: текст не помещается по высоте "
                             f"({need:.0f} > {r.height:.0f} pt): {s[:70]!r}")
        y0 = r.y0 + (r.height - need) / 2 if valign == "middle" else r.y0
        maxw = 0.0
        for i, ln in enumerate(lines):
            w = font.text_length(ln, fontsize=size)
            maxw = max(maxw, w)
            x = r.x0 if align == 0 else (r.x0 + (r.width - w) / 2 if align == 1 else r.x1 - w)
            base = y0 + i * step + (step - size) / 2 + size * font.ascender
            self.p.insert_text((x, base), ln, fontsize=size,
                               fontname="ab" if bold else "ar",
                               fontfile=FB if bold else FR, color=color)
        tx0 = r.x0 if align == 0 else (r.x0 + (r.width - maxw) / 2 if align == 1 else r.x1 - maxw)
        tight = fitz.Rect(tx0, y0, tx0 + maxw, y0 + need)
        if parent is not None:
            pr = fitz.Rect(parent)
            if not (pr.x0 <= tight.x0 and tight.x1 <= pr.x1 and pr.y0 <= tight.y0 and tight.y1 <= pr.y1):
                raise SystemExit(f"{where}: текст выходит за свою карточку: {s[:70]!r}")
        elif register:
            self.blocks.append((f"текст «{s[:28]}»", "rect", tight, True))
        return y0 + need

    # -- фигуры
    def card(self, rect, pal, name, radius=12, width=1.4):
        r = fitz.Rect(rect)
        fill, border = PAL[pal]
        rel = min(0.5, radius / min(r.width, r.height))
        self.p.draw_rect(r, color=border, fill=fill, width=width, radius=rel)
        self.blocks.append((name, "rect", r, True))
        return r

    def circle(self, c, rad, pal, name, width=2.0):
        fill, border = PAL[pal]
        c = fitz.Point(c)
        self.p.draw_circle(c, rad, color=border, fill=fill, width=width)
        self.blocks.append((name, "circle", (c, rad), True))

    def disc(self, c, rad, color, label=None, parent=None, size=14):
        c = fitz.Point(c)
        self.p.draw_circle(c, rad, color=color, fill=color, width=0)
        if label is not None:
            self.text((c.x - rad, c.y - rad, c.x + rad, c.y + rad), label, size,
                      True, WHITE, align=1, valign="middle", lh=1.0, parent=parent)

    def arrow(self, pts, src, dst, color=MUTED, width=2.0):
        pts = [fitz.Point(p) for p in pts]
        self.p.draw_polyline(pts, color=color, width=width)
        a, b = pts[-2], pts[-1]
        ang = math.atan2(b.y - a.y, b.x - a.x)
        s = 9
        head = [b,
                fitz.Point(b.x - s * math.cos(ang - 0.45), b.y - s * math.sin(ang - 0.45)),
                fitz.Point(b.x - s * math.cos(ang + 0.45), b.y - s * math.sin(ang + 0.45))]
        self.p.draw_polyline(head, color=color, fill=color, width=0.5, closePath=True)
        self.arrows.append((pts, src, dst))

    # -- оформление слайда
    def chrome(self, kicker, title):
        border = PAL[self.accent][1]
        self.p.draw_rect(fitz.Rect(0, 0, 8, H), color=None, fill=border, width=0)
        self.text((M, 30, W - M, 45), kicker.upper(), 11, True, border, lh=1.2)
        self.text((M, 50, W - M, 86), title, 26, True, INK, lh=1.2)
        self.p.draw_line((M, H - 34), (W - M, H - 34), color=LINE, width=0.8)
        self.text((M, H - 26, W / 2, H - 12), f"{DECK} · {VERSION} · внутренний документ", 9, False, MUTED,
                  lh=1.2, register=False)
        self.text((W / 2, H - 26, W - M, H - 12), str(self.n), 9, True, MUTED,
                  align=2, lh=1.2, register=False)


def info_card(sl, rect, pal, title, body=None, num=None, tsize=17, bsize=14,
              stack=False, pad=18, title_color=None):
    r = sl.card(rect, pal, name=f"карточка «{title[:24]}»")
    border = PAL[pal][1]
    x0, y = r.x0 + pad, r.y0 + pad
    inner = fitz.Rect(r.x0 + 1, r.y0 + 1, r.x1 - 1, r.y1 - 1)
    if num is not None:
        cr = 14
        c = fitz.Point(x0 + cr, y + cr)
        sl.disc(c, cr, border, str(num), parent=inner)
        if stack:
            y = y + 2 * cr + 10
            y = sl.text((x0, y, r.x1 - pad, r.y1 - pad), title, tsize, True,
                        title_color or INK, parent=inner) + 6
        else:
            bottom = sl.text((x0 + 2 * cr + 12, y + 3, r.x1 - pad, r.y1 - pad), title, tsize,
                             True, title_color or INK, parent=inner)
            y = max(bottom, y + 2 * cr) + 8
    else:
        y = sl.text((x0, y, r.x1 - pad, r.y1 - pad), title, tsize, True,
                    title_color or INK, parent=inner) + 8
    if body:
        sl.text((x0, y, r.x1 - pad, r.y1 - pad), body, bsize, False, MUTED, parent=inner)
    return r


def bullets(sl, x0, y, x1, y_max, items, size, color, parent):
    for it in items:
        sl.disc((x0 + 4, y + size * 0.68), 3.5, color)
        y = sl.text((x0 + 16, y, x1, y_max), it, size, False, INK, parent=parent) + 6
    return y


def harrow(sl, a, b, y, src, dst):
    sl.arrow([(a.x1 + 2, y), (b.x0 - 2, y)], src, dst)


def varrow(sl, a, b, x, src, dst):
    sl.arrow([(x, a.y1 + 2), (x, b.y0 - 2)], src, dst)


def circle_edge(c, rad, toward, gap=2.0):
    dx, dy = toward[0] - c[0], toward[1] - c[1]
    d = math.hypot(dx, dy)
    return (c[0] + dx / d * (rad + gap), c[1] + dy / d * (rad + gap))


def name_of(sl, rect):
    for nm, kind, g, _ in sl.blocks:
        if kind == "rect" and fitz.Rect(g) == fitz.Rect(rect):
            return nm
    raise SystemExit(f"Слайд {sl.n}: у стрелки нет блока {rect}")


# ------------------------------------------------------ проверка геометрии
def bbox(blk):
    _, kind, g, _ = blk
    if kind == "rect":
        return fitz.Rect(g)
    c, rad = g
    return fitz.Rect(c.x - rad, c.y - rad, c.x + rad, c.y + rad)


def overlaps(a, b):
    ka, ga, kb, gb = a[1], a[2], b[1], b[2]
    if ka == "rect" and kb == "rect":
        i = fitz.Rect(ga) & fitz.Rect(gb)
        return (not i.is_empty) and i.width > 0.5 and i.height > 0.5
    if ka == "circle" and kb == "circle":
        (c1, r1), (c2, r2) = ga, gb
        return math.hypot(c1.x - c2.x, c1.y - c2.y) < r1 + r2 - 0.5
    if ka == "circle":
        ga, gb = gb, ga
    r = fitz.Rect(ga)
    c, rad = gb
    nx, ny = min(max(c.x, r.x0), r.x1), min(max(c.y, r.y0), r.y1)
    return math.hypot(c.x - nx, c.y - ny) < rad - 0.5


def inside(pt, blk, pad=1.5):
    _, kind, g, _ = blk
    if kind == "rect":
        r = fitz.Rect(g)
        return r.x0 + pad < pt.x < r.x1 - pad and r.y0 + pad < pt.y < r.y1 - pad
    c, rad = g
    return math.hypot(pt.x - c.x, pt.y - c.y) < rad - pad


def dist_to_edge(pt, blk):
    _, kind, g, _ = blk
    if kind == "circle":
        c, rad = g
        return abs(math.hypot(pt.x - c.x, pt.y - c.y) - rad)
    r = fitz.Rect(g)
    dx = max(r.x0 - pt.x, 0, pt.x - r.x1)
    dy = max(r.y0 - pt.y, 0, pt.y - r.y1)
    if dx == 0 and dy == 0:
        return min(pt.x - r.x0, r.x1 - pt.x, pt.y - r.y0, r.y1 - pt.y)
    return math.hypot(dx, dy)


def check_geometry(sl):
    errs = []
    area = fitz.Rect(12, 12, W - 12, CONTENT_BOTTOM)
    for b in sl.blocks:
        bb = bbox(b)
        if not (area.x0 <= bb.x0 and bb.x1 <= area.x1 and area.y0 <= bb.y0 and bb.y1 <= area.y1):
            errs.append(f"{b[0]} выходит за рабочую область слайда {tuple(round(v) for v in bb)}")
    for i in range(len(sl.blocks)):
        for j in range(i + 1, len(sl.blocks)):
            if overlaps(sl.blocks[i], sl.blocks[j]):
                errs.append(f"{sl.blocks[i][0]} перекрывается с {sl.blocks[j][0]}")
    by_name = {b[0]: b for b in sl.blocks}
    for pts, src, dst in sl.arrows:
        for nm in (src, dst):
            if nm not in by_name:
                errs.append(f"стрелка ссылается на неизвестный блок {nm}")
        if src in by_name and dist_to_edge(pts[0], by_name[src]) > 6:
            errs.append(f"стрелка {src} → {dst} начинается не у края источника")
        if dst in by_name and dist_to_edge(pts[-1], by_name[dst]) > 6:
            errs.append(f"стрелка {src} → {dst} не доходит до края цели")
        hit = None
        for k in range(len(pts) - 1):
            a, b = pts[k], pts[k + 1]
            steps = max(2, int(math.hypot(b.x - a.x, b.y - a.y) / 2))
            for s in range(steps + 1):
                t = s / steps
                pt = fitz.Point(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t)
                for blk in sl.blocks:
                    if inside(pt, blk):
                        hit = blk[0]
                        break
                if hit:
                    break
            if hit:
                break
        if hit:
            errs.append(f"стрелка {src} → {dst} проходит внутри блока {hit}")
    return [f"Слайд {sl.n}: {e}" for e in errs]


# ================================================================= слайды
doc = fitz.open()
slides = []


def new(accent):
    sl = Slide(doc, len(slides) + 1, accent)
    slides.append(sl)
    return sl


def cell(sl, rect, pal, num, title, body, example, tsize=15.5, bsize=12.5, esize=10.5):
    """Ячейка схемы: номер, заголовок, что делает, пример мелким шрифтом."""
    r = sl.card(rect, pal, name=f"ячейка «{title[:22]}»")
    inner = fitz.Rect(r.x0 + 1, r.y0 + 1, r.x1 - 1, r.y1 - 1)
    border = PAL[pal][1]
    pad = 14
    x0, y = r.x0 + pad, r.y0 + pad
    cr = 12
    sl.disc((x0 + cr, y + cr), cr, border, str(num), parent=inner, size=12)
    bottom = sl.text((x0 + 2 * cr + 8, y + 2, r.x1 - pad, r.y1 - pad), title, tsize, True, INK, parent=inner)
    y = max(bottom, y + 2 * cr) + 7
    y = sl.text((x0, y, r.x1 - pad, r.y1 - pad), body, bsize, False, MUTED, parent=inner) + 7
    sl.text((x0, y, r.x1 - pad, r.y1 - pad), example, esize, False, border, parent=inner, lh=1.3)
    return r


# 1. Как устроен агент -----------------------------------------------------
sl = new("green")
sl.chrome("Как это работает", "Шесть шагов, каждое утро, без вашего участия")
CW = (W - 2 * M - 2 * 18) / 3
CH = 186
ROW1, ROW2 = 104, 304
cells = [
    ("blue", 1, "Сбор в 06:30",
     "Обходит площадки по профилям наших должностей и по трём радиусам: Краснодар, удалённо, релокация.",
     "Пример: за одно утро 312 резюме с hh и «Работы России» и 38 строк из выгрузки Авито."),
    ("blue", 2, "Сшивка досье",
     "Разбирает резюме на поля, находит того же человека на других площадках и в соцсетях, убирает дубли.",
     "Пример: резюме на hh, профиль на Хабр Карьере и GitHub со STM32 — это один человек и одна карточка."),
    ("blue", 3, "Монитор изменений",
     "Хранит каждую версию резюме и сравнивает их. Изменение превращается в событие.",
     "Пример: 2 августа поднял ожидания со 165 до 190 тысяч, 26 сентября открыл резюме для поиска."),
    ("green", 4, "Оценка по профилям",
     "Считает соответствие всем 83 должностям, а не только открытым вакансиям. Балл и разбор.",
     "Пример: 88 из 100 по ведущему электронщику: Altium — 20, отрасль — 15, вывод в серию не подтверждён — 4 из 10."),
    ("sand", 5, "Предложение человеку",
     "Готовит письмо, приглашение, напоминание — и останавливается. Отправки без нажатия не бывает.",
     "Пример: письмо готово в 07:00, ушло в 09:12, после того как рекрутер поправил одну фразу."),
    ("violet", 6, "Список рекрутеру",
     "В 08:30 дайджест в интерфейсе и на почте: кто появился, у кого что изменилось, кому пора напомнить.",
     "Пример: пять человек в списке — двоих взяли в работу, одного отложили на три месяца, одного закрыли."),
]
for i, (pal, num, t, b, e) in enumerate(cells):
    x = M + (i % 3) * (CW + 18)
    y = ROW1 if i < 3 else ROW2
    cell(sl, (x, y, x + CW, y + CH), pal, num, t, b, e)

# 2. Как человек попадает в базу -------------------------------------------
sl = new("violet")
sl.chrome("Откуда берётся база", "Агент копит сам, рекрутер расставляет приоритеты")
sl.text((M, 100, W - M, 150),
        "База наполняется без заявок и без вакансий: всё, что агент нашёл и оценил выше порога, "
        "остаётся под наблюдением и обновляется годами. Рекрутер решает, кого поднять в приоритете.",
        15, False, INK, lh=1.35)
lanes = [
    ("grey", "Увидел, но мимо", "Балл ниже 60 ни по одной из 83 должностей.",
     "Карточка не заводится. Если человек сменит работу и подойдёт — появится сам."),
    ("blue", "Наблюдение", "Балл 60 и выше хотя бы по одной должности. Решает агент, без человека.",
     "Хранится без контактов: роль, опыт, навыки, соцсети, история резюме. Обновляется при каждом изменении."),
    ("violet", "Веду особо", "Рекрутер нажал «вести особо». Порог для этого человека отменяется.",
     "Уведомление приходит на любое изменение: сменил работу, открыл поиск, поднял ожидания."),
    ("green", "Кандидат", "Рекрутер нажал «взять в работу».",
     "Открываются контакты, заводится статус и история. Дальше — воронка и переписка."),
]
LW = (W - 2 * M - 3 * 16) / 4
for i, (pal, t, who, what) in enumerate(lanes):
    x = M + i * (LW + 16)
    r = sl.card((x, 168, x + LW, 420), pal, name=f"полоса «{t}»")
    inner = fitz.Rect(r.x0 + 1, r.y0 + 1, r.x1 - 1, r.y1 - 1)
    y = sl.text((r.x0 + 14, r.y0 + 16, r.x1 - 14, r.y1 - 14), t, 17, True, PAL[pal][1], parent=inner) + 8
    y = sl.text((r.x0 + 14, y, r.x1 - 14, r.y1 - 14), who, 12.5, True, INK, parent=inner, lh=1.3) + 8
    sl.text((r.x0 + 14, y, r.x1 - 14, r.y1 - 14), what, 12, False, MUTED, parent=inner, lh=1.35)
r = sl.card((M, 436, W - M, 492), "sand", name="итог по базе")
sl.text((M + 18, 444, W - M - 18, 486),
        "Порог 60 — настройка, а не закон: его двигает руководитель подбора, когда карточек приходит "
        "слишком много или слишком мало.", 14, True, PAL["sand"][1], parent=r, valign="middle", lh=1.3)

# 3. Один день -------------------------------------------------------------
sl = new("blue")
sl.chrome("Один день", "Что происходит без вас и что с вами")
rows = [
    ("06:30", "Агент обходит площадки", "312 резюме, из них 43 новых", "blue"),
    ("06:40", "Смотрит, что изменилось", "17 событий: сменил работу, открыл поиск, добавил навык", "blue"),
    ("06:58", "Считает баллы и отбирает", "выше порога — пятеро", "blue"),
    ("07:00", "Готовит письма и останавливается", "отправка заблокирована до подтверждения", "sand"),
    ("08:30", "Присылает список", "5 карточек, 3 подтверждения, 2 зависших, 2 встречи", "sand"),
    ("09:00", "Вы разбираете утро", "двое в работу, один отложен, один не наш", "white"),
    ("10:15", "Кандидат ответил", "вы назначаете встречу, статус меняется сам", "white"),
    ("18:00", "Вечерний прогон и отчёт", "за неделю: 14 писем, 6 ответов, 3 интервью, 1 оффер", "blue"),
]
y = 104
for t, head, note, pal in rows:
    r = sl.card((M, y, W - M, y + 44), pal, name=f"строка {t}", radius=8, width=1.0)
    inner = fitz.Rect(r.x0 + 1, r.y0 + 1, r.x1 - 1, r.y1 - 1)
    sl.text((r.x0 + 16, r.y0 + 8, r.x0 + 80, r.y1 - 8), t, 14, True, INK, valign="middle", parent=inner)
    sl.text((r.x0 + 96, r.y0 + 8, r.x0 + 400, r.y1 - 8), head, 14, False, INK, valign="middle", parent=inner)
    sl.text((r.x0 + 412, r.y0 + 8, r.x1 - 16, r.y1 - 8), note, 12.5, False, MUTED, valign="middle", parent=inner)
    y += 50

# 4. Экран «Сегодня» -------------------------------------------------------
sl = new("green")
sl.chrome("Ваше утро", "Экран «Сегодня»: четыре блока по срочности")
HW = (W - 2 * M - 20) / 2
info_card(sl, (M, 104, M + HW, 268), "green", "Обратите внимание",
          "Кто появился и у кого что изменилось. Балл, должность и строка «почему сегодня». "
          "Три кнопки: в работу, отложить на три месяца, не наш с причиной.", num=1, tsize=17, bsize=13.5)
info_card(sl, (M + HW + 20, 104, W - M, 268), "sand", "Ждут подтверждения",
          "Письма, приглашения, напоминания, спорные дубли и найденные профили в соцсетях. "
          "Текст письма правится прямо здесь, до отправки.", num=2, tsize=17, bsize=13.5)
info_card(sl, (M, 288, M + HW, 430), "blue", "Зависшие",
          "Кто молчит пятый день. Агент предлагает напоминание, решение за вами.", num=3, tsize=17, bsize=13.5)
info_card(sl, (M + HW + 20, 288, W - M, 430), "blue", "Сегодня и завтра",
          "Интервью и запланированные касания. Вопросы для скрининга подготовлены заранее.",
          num=4, tsize=17, bsize=13.5)
r = sl.card((M, 438, W - M, 498), "grey", name="правила экрана")
inner = fitz.Rect(r.x0 + 1, r.y0 + 1, r.x1 - 1, r.y1 - 1)
yy = sl.text((M + 18, 446, W - M - 18, 492),
             "«Отложить» и «не наш» открывают окно: причину нужно описать, не меньше 200 знаков.",
             13, True, INK, parent=inner) + 4
sl.text((M + 18, yy, W - M - 18, 492),
        "Текст уходит агенту и настраивает веса. Больше двенадцати карточек за утро — "
        "сигнал, что пороги сбиты.", 12.5, False, MUTED, parent=inner, lh=1.3)

# 5. Балл и профиль --------------------------------------------------------
sl = new("sand")
sl.chrome("Оценка", "Балл — порядок просмотра. Критерии задаёте вы")
feats = [("Опыт по профилю, 9 лет", 18, 20), ("Инструменты: Altium, трассировка", 20, 20),
         ("Отрасль: приборостроение", 15, 15), ("Держится на месте в среднем 3 года", 14, 15),
         ("География: Краснодар", 10, 10), ("Ожидания против вилки", 7, 10),
         ("Вывод изделия в серию — не подтверждён", 4, 10)]
y = 104
BARX, BARW = 560, 250
for name, v, mx in feats:
    sl.text((M, y, BARX - 20, y + 18), name, 13, False, INK)
    sl.p.draw_rect(fitz.Rect(BARX, y + 3, BARX + BARW, y + 15), color=None, fill=PAL["grey"][0], width=0, radius=0.5)
    pal = "green" if v / mx >= 0.75 else "sand"
    sl.p.draw_rect(fitz.Rect(BARX, y + 3, BARX + BARW * v / mx, y + 15), color=None,
                   fill=PAL[pal][1], width=0, radius=0.5)
    sl.text((BARX + BARW + 12, y, W - M, y + 18), f"{v} / {mx}", 12.5, False, MUTED)
    y += 26
r = sl.card((M, 296, W - M, 396), "violet", name="настройка профиля")
inner = fitz.Rect(r.x0 + 1, r.y0 + 1, r.x1 - 1, r.y1 - 1)
yy = sl.text((M + 18, 308, W - M - 18, 390), "Жёстких критериев в системе нет", 16, True,
             PAL["violet"][1], parent=inner) + 6
sl.text((M + 18, yy, W - M - 18, 390),
        "Портрет любой из 83 должностей правит любой рекрутер, а не только руководитель: требования, "
        "ключевые слова и синонимы, доноры, вилка, география, веса. Сохранение создаёт новую версию с вашим "
        "именем, её утверждает руководитель подбора; до утверждения выдача идёт по прежней версии, "
        "поэтому испортить работу правкой нельзя.",
        12.5, False, MUTED, parent=inner, lh=1.3)
r2 = sl.card((M, 404, W - M, 492), "green", name="как править")
inner2 = fitz.Rect(r2.x0 + 1, r2.y0 + 1, r2.x1 - 1, r2.y1 - 1)
yy = sl.text((M + 18, 414, W - M - 18, 486), "Порядок правки", 14.5, True, PAL["green"][1], parent=inner2) + 5
bullets(sl, M + 18, yy, W - M - 18, 488, [
    "Меняйте по одному признаку за раз, иначе не поймёте, что сработало.",
    "Перед сохранением — «Предпросмотр»: сколько людей пройдёт и кто выпадет.",
], 12.5, PAL["green"][1], inner2)

# 6. Досье: резюме и соцсети ----------------------------------------------
sl = new("blue")
sl.chrome("Досье кандидата", "Резюме — половина картины. Вторая половина в сети")
sl.text((M, 100, W - M, 144),
        "По каждому интересному человеку агент ищет профессиональные следы в открытых источниках и "
        "прикладывает их к карточке. Каждое совпадение помечено: подтверждено или вероятно.",
        15, False, INK, lh=1.35)
srcs = [
    ("blue", "GitHub и GitLab", "Репозитории, язык, активность, дата последнего коммита.",
     "Для электронщиков и программистов это честнее резюме: видно, что человек делает руками."),
    ("violet", "Telegram: профильные чаты", "Ник, тематика сообщений, экспертность в ответах.",
     "Помогает понять уровень и интересы. В чатах же видно, ищет ли человек работу."),
    ("green", "ВКонтакте и открытые профили", "Город, работодатель, профессиональные публикации.",
     "В досье переносится только то, что относится к работе. Личное не собираем и не храним."),
    ("sand", "Хабр Карьера, отраслевые форумы", "Профиль, статьи, ответы, участие в сообществах.",
     "Часто это второй профиль того же человека — агент сшивает его с резюме."),
]
SW = (W - 2 * M - 18) / 2
for i, (pal, t, b, e) in enumerate(srcs):
    x = M + (i % 2) * (SW + 18)
    y = 160 if i < 2 else 288
    cell(sl, (x, y, x + SW, y + 116), pal, i + 1, t, b, e, tsize=15, bsize=12.5, esize=10.5)
r = sl.card((M, 416, W - M, 500), "grey", name="правило соцсетей")
inner = fitz.Rect(r.x0 + 1, r.y0 + 1, r.x1 - 1, r.y1 - 1)
yy = sl.text((M + 18, 426, W - M - 18, 494), "Правило", 14, True, INK, parent=inner) + 5
sl.text((M + 18, yy, W - M - 18, 494),
        "Ищем по имени, нику, почте и работодателю, берём только открытое. Вероятное совпадение подтверждает "
        "рекрутер: однофамилец в досье хуже, чем пустое поле.", 12.5, False, MUTED, parent=inner, lh=1.35)

# 7. Откуда берутся вакансии ----------------------------------------------
sl = new("green")
sl.chrome("Вакансии", "Три пути, которыми вакансия попадает в систему")
paths = [
    ("green", 1, "Рекрутер заводит сам",
     "Форма: должность из списка 83 ролей, число ставок, вилка, срок, нанимающий руководитель.",
     "Агент сразу показывает подходящих из накопленной базы — обычно это десятки людей, за которыми он уже следил."),
    ("blue", 2, "Заявка из 1С",
     "Заявки на подбор заводятся и согласовываются в 1С, сюда приходят выгрузкой.",
     "В рабочем месте их не создают: подразделение, ставки, срок и дата согласования берутся из 1С."),
    ("sand", 3, "Публикация на hh",
     "Опубликованную компанией вакансию агент подхватывает сам, вместе с откликами.",
     "Закрытый поиск по непубличным позициям так не заводится — для него первые два пути."),
]
PW = (W - 2 * M - 2 * 18) / 3
for i, (pal, num, t, b, e) in enumerate(paths):
    x = M + i * (PW + 18)
    cell(sl, (x, 104, x + PW, 104 + 196), pal, num, t, b, e, tsize=16, bsize=13, esize=11)
r = sl.card((M, 322, W - M, 492), "violet", name="что дальше")
inner = fitz.Rect(r.x0 + 1, r.y0 + 1, r.x1 - 1, r.y1 - 1)
yy = sl.text((M + 20, 334, W - M - 20, 486), "Что происходит, когда вакансия появилась", 17, True,
             PAL["violet"][1], parent=inner) + 8
bullets(sl, M + 20, yy, W - M - 20, 486, [
    "Кнопка «показать подходящих» открывает базу с фильтрами вакансии и сортировкой по убыванию балла.",
    "Закрыть вакансию можно только с комментарием от 200 знаков: чем закрыта и что учесть в следующий раз.",
    "Закрытие не останавливает подбор: портрет должности активен, агент копит подходящих на будущее.",
], 12.5, PAL["violet"][1], inner)

# 8. Воронка ---------------------------------------------------------------
sl = new("blue")
sl.chrome("Воронка", "Пятнадцать статусов — те же, что у вас сейчас")
row1 = ["Новый", "Отобран агентом", "Написали", "Ответил", "Телефонное интервью"]
row2 = ["Встреча с руководителем", "Тестовое", "Проверка СБ", "Оффер", "Вышел"]
BW = (W - 2 * M - 4 * 14) / 5
for i, t in enumerate(row1):
    x = M + i * (BW + 14)
    r = sl.card((x, 112, x + BW, 176), "blue", name=f"этап {t}", radius=8)
    sl.text((r.x0 + 8, r.y0 + 8, r.x1 - 8, r.y1 - 8), t, 13, False, INK, align=1, valign="middle", parent=r)
for i, t in enumerate(row2):
    x = M + i * (BW + 14)
    r = sl.card((x, 220, x + BW, 284), "white", name=f"этап2 {t}", radius=8)
    sl.text((r.x0 + 8, r.y0 + 8, r.x1 - 8, r.y1 - 8), t, 13, False, INK, align=1, valign="middle", parent=r)
side = sl.card((M, 320, W - M, 400), "grey", name="боковые статусы")
inner = fitz.Rect(side.x0 + 1, side.y0 + 1, side.x1 - 1, side.y1 - 1)
yy = sl.text((M + 18, 330, W - M - 18, 394), "Боковые статусы", 14, True, INK, parent=inner) + 5
sl.text((M + 18, yy, W - M - 18, 394),
        "Отказался общаться · Не вышел · Резерв · Отказ с нашей стороны · Отказ кандидата. "
        "Резерв напоминает о себе через три месяца.", 12.5, False, MUTED, parent=inner, lh=1.35)
r = sl.card((M, 414, W - M, 492), "green", name="кто меняет статус")
inner = fitz.Rect(r.x0 + 1, r.y0 + 1, r.x1 - 1, r.y1 - 1)
yy = sl.text((M + 18, 424, W - M - 18, 486), "Кто двигает кандидата", 14, True, PAL["green"][1], parent=inner) + 5
sl.text((M + 18, yy, W - M - 18, 486),
        "Статус меняете вы или нанимающий руководитель. Каждое изменение записывается с именем и временем — "
        "по этой истории потом считается конверсия и срок закрытия.", 12.5, False, MUTED, parent=inner, lh=1.35)

# 9. Как учить агента ------------------------------------------------------
sl = new("violet")
sl.chrome("Обратная связь", "Как учить агента: это ваши обычные действия")
ways = [
    ("violet", 1, "Причина отказа", "Нажали «не наш» — выберите причину из списка или напишите словами.",
     "Ночью пересчитываются веса: три отказа из-за переезда — и география станет весить больше."),
    ("blue", 2, "Правка письма", "Исправили текст перед отправкой — правка сохраняется.",
     "Через несколько раз шаблон для этой должности станет вашим, а не общим."),
    ("green", 3, "Оценка карточки", "Под разбором балла две кнопки: «полезная» и «мусор».",
     "Это влияет на пороги: сколько карточек приходит утром и какого качества."),
    ("sand", 4, "Чат обучения", "Отдельный экран: замечания, рекомендации, критика, вопросы.",
     "Пишете как в мессенджере. Причины отказов попадают туда же автоматически."),
]
WW = (W - 2 * M - 18) / 2
for i, (pal, num, t, b, e) in enumerate(ways):
    x = M + (i % 2) * (WW + 18)
    y = 104 if i < 2 else 246
    cell(sl, (x, y, x + WW, y + 134), pal, num, t, b, e, tsize=15, bsize=12.5, esize=10.5)
r = sl.card((M, 392, W - M, 500), "green", name="недельный круг")
inner = fitz.Rect(r.x0 + 1, r.y0 + 1, r.x1 - 1, r.y1 - 1)
yy = sl.text((M + 20, 400, W - M - 20, 494), "Что происходит с написанным: круг замыкается за неделю",
             15, True, PAL["green"][1], parent=inner) + 6
bullets(sl, M + 20, yy, W - M - 20, 494, [
    "Агент копит сообщения и ищет повторяющееся: одна жалоба — ничего, три об одном — предложение.",
    "В пятницу руководителю отдела персонала уходит сводка: что изменить, на чём основано и вопрос по каждому пункту.",
    "Он отвечает «принять», «отклонить» или «обсудить». Принятое применяется ночью и попадает в журнал изменений.",
], 12.5, PAL["green"][1], inner)

# 10. Чего агент не делает -------------------------------------------------
sl = new("red")
sl.chrome("Границы", "Чего агент не делает: это запрет в системе, а не обещание")
nots = ["не пишет кандидату", "не звонит", "не отправляет тестовое задание", "не сообщает об отказе",
        "не публикует вакансии", "не вносит данные в 1С", "не называет зарплату"]
y = 104
for t in nots:
    r = sl.card((M, y, M + 400, y + 42), "red", name=f"запрет «{t[:20]}»", radius=8, width=1.0)
    sl.text((r.x0 + 16, r.y0 + 8, r.x1 - 12, r.y1 - 8), t, 13.5, False, INK, valign="middle", parent=r)
    y += 48
r = sl.card((M + 420, 104, W - M, 440), "green", name="что остаётся вам")
inner = fitz.Rect(r.x0 + 1, r.y0 + 1, r.x1 - 1, r.y1 - 1)
yy = sl.text((r.x0 + 20, r.y0 + 20, r.x1 - 20, r.y1 - 20), "Что остаётся вам", 18, True,
             PAL["green"][1], parent=inner) + 10
yy = bullets(sl, r.x0 + 20, yy, r.x1 - 20, r.y1 - 20, [
    "Оценка человека при встрече и разговор о мотивации.",
    "Решение о найме и торг по деньгам.",
    "Портрет должности: кто нам нужен и по каким признакам это видно.",
    "Подтверждение спорных совпадений в соцсетях.",
], 13, PAL["green"][1], inner) + 6
sl.text((r.x0 + 20, yy, r.x1 - 20, r.y1 - 20),
        "Агент забирает поиск и слежение за рынком — то, что сейчас делается руками по одной площадке.",
        12.5, False, MUTED, parent=inner, lh=1.35)

# 11. Что нужно от вас -----------------------------------------------------
sl = new("green")
sl.chrome("Первый месяц", "Что нужно от службы подбора, чтобы это заработало")
todo = [
    ("Утвердить профили должностей", "Черновик собран из должностной инструкции. Веса, доноров, вилку и "
     "географию ставите вы: без этого агент носит не тех."),
    ("Разбирать утро каждый день", "Пропущенный день — это не «посмотрю завтра», а потерянные люди: "
     "резюме обновляются и уходят к другим."),
    ("Всегда указывать причину отказа", "Единственный способ объяснить агенту, что он ошибся. "
     "Отказ без причины не учит ничему."),
    ("Писать замечания сразу", "Две-три ссылки на карточки — и причина находится за час, а не за месяц."),
    ("Сверять балл с итогом найма", "Вышедший человек с баллом 62 означает, что профиль занижает подходящих."),
]
y = 104
for i, (head, note) in enumerate(todo):
    r = sl.card((M, y, W - M, y + 72), "green" if i < 2 else "white", name=f"пункт {i+1}")
    inner = fitz.Rect(r.x0 + 1, r.y0 + 1, r.x1 - 1, r.y1 - 1)
    cr = 13
    sl.disc((r.x0 + 20 + cr, r.y0 + 18 + cr), cr, PAL["green"][1], str(i + 1), parent=inner, size=12)
    yy = sl.text((r.x0 + 20 + 2 * cr + 12, r.y0 + 16, r.x1 - 20, r.y1 - 12), head, 15, True, INK, parent=inner) + 5
    sl.text((r.x0 + 20 + 2 * cr + 12, yy, r.x1 - 20, r.y1 - 12), note, 12.5, False, MUTED, parent=inner, lh=1.3)
    y += 78


# ============================================================ проверка и запись
errors = []
for s in slides:
    errors += check_geometry(s)
if errors:
    print("ГЕОМЕТРИЯ НЕ ПРОШЛА ПРОВЕРКУ:")
    for e in errors:
        print("  -", e)
    sys.exit(1)

doc.set_metadata({
    "title": "Агент подбора персонала — как работает система",
    "subject": "Как работает агент подбора персонала: для службы подбора",
    "creator": "build_deck_recruiters.py",
})
doc.subset_fonts()
doc.save(OUT, garbage=4, deflate=True)
print(f"saved {OUT}: {len(slides)} слайдов, геометрия проверена, "
      f"блоков {sum(len(s.blocks) for s in slides)}, стрелок {sum(len(s.arrows) for s in slides)}")

if PREVIEW_DIR:
    out = fitz.open(OUT)
    for i, page in enumerate(out):
        page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25)).save(f"{PREVIEW_DIR}/slide_{i + 1:02d}.png")
    print(f"превью: {PREVIEW_DIR}")
