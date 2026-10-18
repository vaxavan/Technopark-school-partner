"""Подготовка фото к публикации.

Площадки принимают не всё, что присылают авторы: Telegram не берёт фото больше 10 МБ и формат
HEIC (фото с iPhone), ВКонтакте и Max тоже капризны к HEIC и огромным снимкам. Поэтому перед
публикацией каждое фото переводится в JPEG: поворот по EXIF, длинная сторона не больше 2560 px,
размер до ~9 МБ. Оригиналы в хранилище не меняются — готовые копии лежат во временной папке."""

import logging
import os
import tempfile
from contextlib import contextmanager

log = logging.getLogger(__name__)

MAX_SIDE = 2560
MAX_BYTES = 9 * 1024 * 1024


def _open(path):
    from PIL import Image

    try:  # HEIC/HEIF — если установлен pillow-heif
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except ImportError:
        pass
    return Image.open(path)


def to_jpeg(path, out_dir):
    """Путь к JPEG-копии или None, если файл не удалось прочитать как изображение."""
    from PIL import Image, ImageOps

    try:
        with _open(path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                background = Image.new("RGB", im.size, "white")
                rgba = im.convert("RGBA")
                background.paste(rgba, mask=rgba.split()[-1])
                im = background
            im = im.convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            out = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + ".jpg")
            for quality in (88, 80, 70, 60):
                im.save(out, "JPEG", quality=quality, optimize=True)
                if os.path.getsize(out) <= MAX_BYTES:
                    break
            return out
    except Exception as exc:  # повреждённый файл или неизвестный формат — публикуем без него
        log.warning("Фото %s не подготовлено: %s", path, exc)
        return None


@contextmanager
def prepared(paths):
    """with prepared(paths) as (jpegs, skipped): ... — временные JPEG-копии и список пропущенных файлов."""
    with tempfile.TemporaryDirectory(prefix="publish-") as tmp:
        jpegs, skipped = [], []
        for i, p in enumerate(paths):
            sub = os.path.join(tmp, str(i))
            os.makedirs(sub)
            out = to_jpeg(p, sub)
            if out:
                jpegs.append(out)
            else:
                skipped.append(os.path.basename(p))
        yield jpegs, skipped
