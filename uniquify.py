# -*- coding: utf-8 -*-
"""
uniquify.py

Модуль отвечает только за "математику" — сборку filter_complex-строки
для ffmpeg на основе того, какие эффекты выбрал пользователь в интерфейсе.
Сам запуск ffmpeg (через FFmpegKit на Android) происходит в main.py —
здесь только генерация команды, чтобы это было легко тестировать отдельно.
"""

import random
import string
import time


def _rand_str(n: int = 12) -> str:
    """Случайная строка для метаданных (comment)."""
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def _rand_creation_time() -> str:
    """Случайная дата создания в пределах последних ~2 лет, формат ffmpeg metadata."""
    now = time.time()
    offset = random.randint(0, 60 * 60 * 24 * 730)  # до 730 дней назад
    t = time.gmtime(now - offset)
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", t)


def build_ffmpeg_command(input_path: str,
                          output_path: str,
                          opts: dict,
                          watermark_path: str | None = None) -> str:
    """
    Собирает полную строку команды ffmpeg на основе словаря opts.

    Ожидаемые ключи opts (все опциональны, если ключа нет — эффект выключен):
      trim_sec        float  — сколько секунд обрезать с начала И с конца
      saturation      float  — изменение насыщенности в % (-20..20)
      hue             float  — сдвиг оттенка в градусах (-15..15)
      noise           float  — сила шума (0..20)
      crop_pct        float  — обрезка краёв в % с каждой стороны (0..8)
      speed_pct       float  — изменение скорости в % (-5..5)
      watermark_opacity float — прозрачность водяного знака в % (10..90),
                                учитывается только если передан watermark_path
      random_metadata bool   — перезаписать метаданные случайными значениями

    watermark_path — путь к картинке для наложения (или None, если не нужно).
    """

    # ---------- 1. Обрезка по времени (через -ss/-t, это быстро и просто) ----------
    ss_args = ""
    t_args = ""
    trim_sec = float(opts.get("trim_sec", 0) or 0)
    if trim_sec > 0:
        # -ss ДО -i => быстрый seek на уровне контейнера.
        ss_args = f"-ss {trim_sec:.3f}"
        # длительность после обрезки посчитает вызывающий код (main.py),
        # т.к. для этого нужна общая длительность файла (через FFprobeKit).
        duration = opts.get("_source_duration")
        if duration:
            new_len = max(duration - 2 * trim_sec, 0.5)
            t_args = f"-t {new_len:.3f}"

    # ---------- 2. Видео-фильтры, применяемые последовательно ----------
    vf_parts = []

    if opts.get("hflip"):
        vf_parts.append("hflip")

    if opts.get("saturation"):
        sat_mult = 1 + float(opts["saturation"]) / 100.0
        vf_parts.append(f"eq=saturation={sat_mult:.4f}")

    if opts.get("hue"):
        vf_parts.append(f"hue=h={float(opts['hue']):.2f}")

    if opts.get("noise"):
        vf_parts.append(f"noise=alls={float(opts['noise']):.1f}:allf=t")

    if opts.get("crop_pct"):
        p = float(opts["crop_pct"]) / 100.0
        p = min(max(p, 0.0), 0.15)  # защита от слишком большой обрезки
        keep = 1 - 2 * p
        vf_parts.append(f"crop=iw*{keep:.4f}:ih*{keep:.4f}")

    speed_factor = None
    if opts.get("speed_pct"):
        speed_factor = 1 + float(opts["speed_pct"]) / 100.0
        # setpts меняет длительность видео обратно пропорционально скорости
        vf_parts.append(f"setpts=PTS/{speed_factor:.4f}")

    video_chain = ",".join(vf_parts) if vf_parts else "null"

    # ---------- 3. Аудио-фильтры ----------
    af_parts = []
    if speed_factor:
        # atempo корректно работает в диапазоне 0.5-2.0, нам этого достаточно
        af_parts.append(f"atempo={speed_factor:.4f}")
    audio_chain = ",".join(af_parts) if af_parts else "anull"

    # ---------- 4. Собираем filter_complex ----------
    inputs = f'-i "{input_path}"'
    if watermark_path:
        inputs += f' -i "{watermark_path}"'
        opacity = float(opts.get("watermark_opacity", 60)) / 100.0
        filter_complex = (
            f'[0:v]{video_chain}[base];'
            f'[1:v]scale=iw*0.18:-1,format=rgba,colorchannelmixer=aa={opacity:.2f}[wm];'
            f'[base][wm]overlay=W-w-20:H-h-20[outv];'
            f'[0:a]{audio_chain}[outa]'
        )
        map_args = '-map "[outv]" -map "[outa]"'
    else:
        filter_complex = f'[0:v]{video_chain}[outv];[0:a]{audio_chain}[outa]'
        map_args = '-map "[outv]" -map "[outa]"'

    # ---------- 5. Метаданные ----------
    meta_args = ""
    if opts.get("random_metadata"):
        meta_args = (
            f'-map_metadata -1 '
            f'-metadata comment="{_rand_str()}" '
            f'-metadata creation_time="{_rand_creation_time()}"'
        )

    # ---------- 6. Итоговая команда ----------
    cmd = (
        f'{ss_args} {inputs} {t_args} '
        f'-filter_complex "{filter_complex}" {map_args} '
        f'{meta_args} '
        f'-c:v libx264 -preset veryfast -crf 23 '
        f'-c:a aac -b:a 128k '
        f'-y "{output_path}"'
    )
    # убираем лишние пробелы
    return " ".join(cmd.split())
