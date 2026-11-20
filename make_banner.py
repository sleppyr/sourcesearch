"""Генерирует banner.jpg для приветствия. Запуск: python make_banner.py (нужен Pillow)."""
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import glob, os

W, H = 1280, 520
FONT_DIRS = ["/usr/share/fonts/opentype/inter", "C:/Windows/Fonts", "/usr/share/fonts/truetype/dejavu"]


def font(size, bold=False):
    names = (["Inter-Bold.otf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"] if bold
             else ["Inter-Medium.otf", "segoeui.ttf", "DejaVuSans.ttf"])
    for d in FONT_DIRS:
        for n in names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# фон: диагональный градиент
bg = Image.new("RGB", (W, H))
px = bg.load()
c1, c2 = (7, 12, 28), (14, 38, 78)
for y in range(H):
    for x in range(W):
        t = (x / W * 0.6 + y / H * 0.4)
        px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))

# мягкое свечение
glow = Image.new("RGB", (W, H), (0, 0, 0))
g = ImageDraw.Draw(glow)
g.ellipse((780, -120, 1380, 420), fill=(30, 110, 200))
g.ellipse((-200, 300, 380, 760), fill=(20, 150, 130))
glow = glow.filter(ImageFilter.GaussianBlur(140))
bg = Image.blend(bg, Image.eval(glow, lambda v: v), 0.0)
bg = Image.composite(bg, bg, Image.new("L", (W, H), 255))
from PIL import ImageChops
bg = ImageChops.add(bg, glow.point(lambda v: int(v * 0.55)))

d = ImageDraw.Draw(bg, "RGBA")
# стеклянная панель
d.rounded_rectangle((36, 36, W - 36, H - 36), 36, fill=(255, 255, 255, 14), outline=(255, 255, 255, 46), width=2)

# логотип
d.rounded_rectangle((84, 84, 164, 164), 24, fill=(56, 189, 248, 255))
d.text((124, 124), "SS", font=font(36, True), fill=(6, 16, 36), anchor="mm")
d.text((184, 124), "SourceSearch", font=font(30, True), fill=(226, 240, 255), anchor="lm")

# заголовок
d.text((84, 250), "Найди свой", font=font(54, True), fill=(255, 255, 255), anchor="lm")
d.text((84, 322), "уникальный юзернейм", font=font(54, True), fill=(56, 189, 248), anchor="lm")
d.text((84, 410), "Быстрый поиск · оценка · трекер · ультра-ники", font=font(25), fill=(160, 184, 214), anchor="lm")

# пузыри чата
def bubble(x, y, w, h, fill, text, sub=None, fg=(255, 255, 255)):
    d.rounded_rectangle((x, y, x + w, y + h), 26, fill=fill)
    d.text((x + 28, y + h // 2 - (12 if sub else 0)), text, font=font(28, True), fill=fg, anchor="lm")
    if sub:
        d.text((x + 28, y + h // 2 + 18), sub, font=font(22), fill=(200, 215, 235), anchor="lm")

bubble(800, 96, 390, 96, (30, 58, 96, 235), "@viufa", "свободен · проверено за 0.4 с")
bubble(850, 214, 340, 96, (16, 120, 100, 235), "72 / 100", "класс: Сильный")
bubble(800, 332, 390, 116, (30, 58, 96, 235), "Ультра-поиск", "ник из настоящих слов")
# индикатор оценки
d.rounded_rectangle((828, 424, 1160, 430), 4, fill=(255, 255, 255, 40))
d.rounded_rectangle((828, 424, 1070, 430), 4, fill=(52, 211, 153, 255))

bg.convert("RGB").save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "banner.jpg"), quality=92)
print("banner.jpg готов")
