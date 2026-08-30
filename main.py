# -*- coding: utf-8 -*-
"""
main.py — Android-приложение "Уникализатор видео".

Интерфейс: выбираешь видео -> отмечаешь галочками нужные эффекты
(и при желании двигаешь ползунки значений) -> жмёшь "Обработать" ->
получаешь готовый файл в Movies/Uniqualizer и можешь сразу поделиться им.

Обработка видео выполняется через FFmpegKit (Android-библиотека,
подключается автоматически при сборке через buildozer.spec) в отдельном
потоке, чтобы интерфейс не зависал во время работы ffmpeg.
"""

import os
import threading
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.checkbox import CheckBox
from kivy.uix.slider import Slider
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.popup import Popup
from kivy.uix.filechooser import FileChooserIconView

from uniquify import build_ffmpeg_command

# Пытаемся импортировать android-специфичные модули.
# На десктопе (для локального теста интерфейса) их нет — не страшно.
try:
    from android.permissions import request_permissions, Permission, check_permission
    from android.storage import primary_external_storage_path
    from jnius import autoclass
    ON_ANDROID = True
except ImportError:
    ON_ANDROID = False

try:
    from plyer import share
except Exception:
    share = None


OUTPUT_DIR_NAME = "Uniqualizer"


def get_movies_dir() -> str:
    """Папка, куда будем сохранять результат."""
    if ON_ANDROID:
        base = primary_external_storage_path()
        path = os.path.join(base, "Movies", OUTPUT_DIR_NAME)
    else:
        path = os.path.join(os.path.expanduser("~"), OUTPUT_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Описание доступных эффектов: (ключ в opts, подпись, мин, макс, значение по умолчанию)
# Если min==max==0 — у эффекта нет ползунка (просто вкл/выкл).
# ---------------------------------------------------------------------------
EFFECTS = [
    ("trim_sec", "Обрезать начало и конец (сек)", 0.0, 3.0, 1.0),
    ("hflip", "Отразить по горизонтали", 0, 0, 0),
    ("saturation", "Изменить насыщенность (%)", -20.0, 20.0, 5.0),
    ("hue", "Сдвинуть оттенок (градусы)", -15.0, 15.0, 5.0),
    ("noise", "Добавить лёгкий шум", 0.0, 20.0, 6.0),
    ("crop_pct", "Обрезать края (%)", 0.0, 8.0, 2.0),
    ("speed_pct", "Изменить скорость (%)", -5.0, 5.0, 2.0),
    ("random_metadata", "Случайные метаданные (дата/комментарий)", 0, 0, 0),
]


class RootWidget(BoxLayout):
    pass


class UniqualizerApp(App):

    def build(self):
        self.selected_video = None
        self.watermark_path = None
        self.row_widgets = {}  # key -> (checkbox, slider или None)

        root = BoxLayout(orientation="vertical", padding=10, spacing=8)

        # --- Кнопка выбора видео ---
        top_bar = BoxLayout(size_hint_y=None, height=50, spacing=8)
        self.video_label = Label(text="Видео не выбрано", size_hint_x=0.7)
        pick_btn = Button(text="Выбрать видео", size_hint_x=0.3)
        pick_btn.bind(on_release=self.open_file_chooser)
        top_bar.add_widget(self.video_label)
        top_bar.add_widget(pick_btn)
        root.add_widget(top_bar)

        # --- Список эффектов с чекбоксами и ползунками ---
        scroll = ScrollView(size_hint=(1, 1))
        grid = GridLayout(cols=1, size_hint_y=None, spacing=6, padding=4)
        grid.bind(minimum_height=grid.setter("height"))

        for key, title, vmin, vmax, vdefault in EFFECTS:
            row = BoxLayout(orientation="vertical", size_hint_y=None, height=70)

            top_row = BoxLayout(size_hint_y=None, height=40)
            cb = CheckBox(size_hint_x=None, width=40)
            lbl = Label(text=title, halign="left", valign="middle")
            lbl.bind(size=lambda w, *a: setattr(w, "text_size", w.size))
            top_row.add_widget(cb)
            top_row.add_widget(lbl)
            row.add_widget(top_row)

            slider = None
            if vmax != vmin:
                slider = Slider(min=vmin, max=vmax, value=vdefault,
                                 size_hint_y=None, height=30, disabled=True)
                row.add_widget(slider)
                cb.bind(active=lambda w, val, s=slider: setattr(s, "disabled", not val))

            self.row_widgets[key] = (cb, slider)
            grid.add_widget(row)

        # --- Отдельно: водяной знак своей картинкой ---
        wm_row = BoxLayout(orientation="vertical", size_hint_y=None, height=110)
        wm_top = BoxLayout(size_hint_y=None, height=40)
        self.wm_cb = CheckBox(size_hint_x=None, width=40)
        wm_top.add_widget(self.wm_cb)
        wm_top.add_widget(Label(text="Наложить водяной знак (картинка)"))
        wm_row.add_widget(wm_top)

        wm_pick_btn = Button(text="Выбрать картинку", size_hint_y=None, height=35)
        wm_pick_btn.bind(on_release=self.open_watermark_chooser)
        wm_row.add_widget(wm_pick_btn)

        self.wm_slider = Slider(min=10, max=90, value=60, size_hint_y=None,
                                 height=30, disabled=True)
        self.wm_cb.bind(active=lambda w, val: setattr(self.wm_slider, "disabled", not val))
        wm_row.add_widget(self.wm_slider)
        grid.add_widget(wm_row)

        scroll.add_widget(grid)
        root.add_widget(scroll)

        # --- Кнопка обработки и статус ---
        self.status_label = Label(text="", size_hint_y=None, height=30)
        root.add_widget(self.status_label)

        process_btn = Button(text="Обработать видео", size_hint_y=None, height=55)
        process_btn.bind(on_release=self.start_processing)
        root.add_widget(process_btn)

        # --- Запрос разрешений на Android ---
        if ON_ANDROID:
            request_permissions([
                Permission.READ_EXTERNAL_STORAGE,
                Permission.WRITE_EXTERNAL_STORAGE,
            ])

        return root

    # -----------------------------------------------------------------
    # Выбор файлов
    # -----------------------------------------------------------------
    def open_file_chooser(self, *_):
        self._show_chooser(self._on_video_chosen, "Movies", "DCIM")

    def open_watermark_chooser(self, *_):
        self._show_chooser(self._on_watermark_chosen, "Pictures", "DCIM")

    def _show_chooser(self, on_choose, *preferred_subdirs):
        base = primary_external_storage_path() if ON_ANDROID else os.path.expanduser("~")
        start_path = base
        for sub in preferred_subdirs:
            candidate = os.path.join(base, sub)
            if os.path.isdir(candidate):
                start_path = candidate
                break

        chooser = FileChooserIconView(path=start_path)
        popup = Popup(title="Выбери файл", content=chooser, size_hint=(0.95, 0.95))

        def choose(*_a):
            if chooser.selection:
                on_choose(chooser.selection[0])
            popup.dismiss()

        btn_box = BoxLayout(size_hint_y=None, height=45)
        ok_btn = Button(text="Выбрать")
        ok_btn.bind(on_release=choose)
        btn_box.add_widget(ok_btn)

        wrapper = BoxLayout(orientation="vertical")
        wrapper.add_widget(chooser)
        wrapper.add_widget(btn_box)
        popup.content = wrapper
        popup.open()

    def _on_video_chosen(self, path):
        self.selected_video = path
        self.video_label.text = os.path.basename(path)

    def _on_watermark_chosen(self, path):
        self.watermark_path = path

    # -----------------------------------------------------------------
    # Сбор выбранных пользователем опций
    # -----------------------------------------------------------------
    def collect_opts(self) -> dict:
        opts = {}
        for key, (cb, slider) in self.row_widgets.items():
            if not cb.active:
                continue
            if slider is not None:
                opts[key] = slider.value
            else:
                opts[key] = True

        if self.wm_cb.active and self.watermark_path:
            opts["watermark_opacity"] = self.wm_slider.value

        return opts

    # -----------------------------------------------------------------
    # Запуск обработки
    # -----------------------------------------------------------------
    def start_processing(self, *_):
        if not self.selected_video:
            self.status_label.text = "Сначала выбери видео!"
            return

        opts = self.collect_opts()
        if not opts:
            self.status_label.text = "Отметь хотя бы один эффект"
            return

        self.status_label.text = "Обработка... это может занять минуту"
        threading.Thread(target=self._process_thread, args=(opts,), daemon=True).start()

    def _process_thread(self, opts: dict):
        try:
            input_path = self.selected_video
            out_dir = get_movies_dir()
            out_name = f"unique_{int(time.time())}.mp4"
            output_path = os.path.join(out_dir, out_name)

            # Если нужна обрезка по времени — сначала узнаём длительность исходника.
            if opts.get("trim_sec"):
                opts["_source_duration"] = self._probe_duration(input_path)

            watermark = self.watermark_path if self.wm_cb.active else None
            command = build_ffmpeg_command(input_path, output_path, opts, watermark)

            if ON_ANDROID:
                self._run_ffmpegkit(command)
            else:
                # Локальный тест на десктопе — используем системный ffmpeg.
                import subprocess
                subprocess.run(["bash", "-lc", "ffmpeg " + command], check=True)

            Clock.schedule_once(lambda dt: self._on_success(output_path))
        except Exception as exc:
            Clock.schedule_once(lambda dt, e=exc: self._on_error(e))

    def _probe_duration(self, path: str) -> float:
        """Получаем длительность видео через FFprobeKit (на Android) либо ffprobe (на ПК)."""
        if ON_ANDROID:
            FFprobeKit = autoclass("com.arthenica.ffmpegkit.FFprobeKit")
            info_session = FFprobeKit.getMediaInformation(path)
            info = info_session.getMediaInformation()
            return float(info.getDuration())
        else:
            import subprocess
            out = subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path
            ])
            return float(out.decode().strip())

    def _run_ffmpegkit(self, command: str):
        """Синхронный запуск ffmpeg-команды через FFmpegKit (мы уже в фоновом потоке)."""
        FFmpegKit = autoclass("com.arthenica.ffmpegkit.FFmpegKit")
        ReturnCode = autoclass("com.arthenica.ffmpegkit.ReturnCode")

        session = FFmpegKit.execute(command)
        if not ReturnCode.isSuccess(session.getReturnCode()):
            logs = session.getFailStackTrace() or session.getOutput()
            raise RuntimeError(f"FFmpeg завершился с ошибкой:\n{logs}")

    # -----------------------------------------------------------------
    # Колбэки результата (выполняются в основном потоке через Clock)
    # -----------------------------------------------------------------
    def _on_success(self, output_path: str):
        self.status_label.text = f"Готово: {os.path.basename(output_path)}"

        content = BoxLayout(orientation="vertical", spacing=10, padding=10)
        content.add_widget(Label(text=f"Файл сохранён:\n{output_path}"))

        btn_row = BoxLayout(size_hint_y=None, height=50, spacing=10)
        share_btn = Button(text="Поделиться")
        close_btn = Button(text="Закрыть")
        btn_row.add_widget(share_btn)
        btn_row.add_widget(close_btn)
        content.add_widget(btn_row)

        popup = Popup(title="Готово!", content=content, size_hint=(0.85, 0.4))
        close_btn.bind(on_release=popup.dismiss)

        def do_share(*_a):
            if share:
                try:
                    share.share_file(output_path)
                except Exception:
                    pass

        share_btn.bind(on_release=do_share)
        popup.open()

    def _on_error(self, exc):
        self.status_label.text = "Ошибка обработки"
        popup = Popup(title="Ошибка",
                       content=Label(text=str(exc)),
                       size_hint=(0.85, 0.5))
        popup.open()


if __name__ == "__main__":
    UniqualizerApp().run()
