# -*- coding: utf-8 -*-
"""
main.py — "Уникализатор видео" (v2, SAF).

Выбор файлов — системный диалог Android (права на хранилище не нужны).
Результат — в Movies/Uniqualizer (через MediaStore), оттуда же "Поделиться".
Все падения (Python и Java) пишутся в:
  Android/data/org.uniqualizer/files/uniqualizer_crash.log
"""

# ===================== ДИАГНОСТИКА ПАДЕНИЙ =====================
import os
import sys
import time
import threading
import traceback


def _crashlog_write(text):
    try:
        print(text, flush=True)  # видно в adb logcat
    except Exception:
        pass
    path = None
    try:
        from jnius import autoclass as _ac
        _d = _ac("org.kivy.android.PythonActivity").mActivity.getExternalFilesDir(None)
        if _d is not None:
            path = os.path.join(_d.getAbsolutePath(), "uniqualizer_crash.log")
    except Exception:
        path = "/sdcard/uniqualizer_crash.log"
    if path:
        try:
            with open(path, "a", encoding="utf-8") as _f:
                _f.write(text)
        except Exception:
            pass


def _py_hook(t, v, tb):
    _crashlog_write("\n=== PYTHON CRASH %s ===\n%s\n" % (
        time.strftime("%Y-%m-%d %H:%M:%S"),
        "".join(traceback.format_exception(t, v, tb))))
    sys.__excepthook__(t, v, tb)


sys.excepthook = _py_hook


def _thread_hook(a):
    _crashlog_write("\n=== THREAD CRASH (%s) ===\n%s\n" % (
        getattr(a.thread, "name", "?"),
        "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback))))


try:
    threading.excepthook = _thread_hook
except Exception:
    pass

try:
    from jnius import autoclass as _ac2, PythonJavaClass, java_method
    _Thread = _ac2("java.lang.Thread")
    _prev_handler = _Thread.getDefaultUncaughtExceptionHandler()

    class _JavaCrashHook(PythonJavaClass):
        __javainterfaces__ = ["java/lang/Thread$UncaughtExceptionHandler"]
        __javacontext__ = "app"

        @java_method("(Ljava/lang/Thread;Ljava/lang/Throwable;)V")
        def uncaughtException(self, thread, throwable):
            try:
                stack = "\n".join("  at " + str(s) for s in throwable.getStackTrace())
                cause = throwable.getCause()
                extra = ("\nCaused by: " + str(cause)) if cause else ""
                _crashlog_write("\n=== JAVA CRASH ===\n%s\n%s%s\n" % (
                    str(throwable), stack, extra))
            except Exception:
                pass
            if _prev_handler is not None:
                _prev_handler.uncaughtException(thread, throwable)

    _Thread.setDefaultUncaughtExceptionHandler(_JavaCrashHook())
except Exception:
    pass
# =================== КОНЕЦ ДИАГНОСТИКИ ===================

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
from kivy.uix.filechooser import FileChooserIconView  # только для теста на ПК

from uniquify import build_ffmpeg_command

try:
    from jnius import autoclass
    ON_ANDROID = True
except ImportError:
    ON_ANDROID = False

if ON_ANDROID:
    try:
        from android.permissions import request_permissions, Permission
    except ImportError:
        request_permissions, Permission = None, None
    try:
        from android.activity import bind as bind_on_activity_result
    except ImportError:
        bind_on_activity_result = None
else:
    try:
        from plyer import share
    except Exception:
        share = None

try:
    from plyer import share
except Exception:
    share = None

OUTPUT_DIR_NAME = "Uniqualizer"
REQ_PICK_VIDEO = 4241
REQ_PICK_IMAGE = 4242
RESULT_OK = -1


def android_context():
    return autoclass("org.kivy.android.PythonActivity").mActivity


def sdk_int():
    return autoclass("android.os.Build$VERSION").SDK_INT


def _copy_file(src_path, dst_path):
    with open(src_path, "rb") as s, open(dst_path, "wb") as d:
        while True:
            chunk = s.read(1024 * 1024)
            if not chunk:
                break
            d.write(chunk)


# (ключ, подпись, мин, макс, по умолчанию; мин==макс==0 — просто вкл/выкл)
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


class UniqualizerApp(App):

    def build(self):
        self._video_uri = None
        self._image_uri = None
        self.watermark_path = None  # только для десктопа
        self.selected_video = None  # только для десктопа
        self.row_widgets = {}

        root = BoxLayout(orientation="vertical", padding=10, spacing=8)

        top_bar = BoxLayout(size_hint_y=None, height=50, spacing=8)
        self.video_label = Label(text="Видео не выбрано", size_hint_x=0.7)
        pick_btn = Button(text="Выбрать видео", size_hint_x=0.3)
        pick_btn.bind(on_release=self.open_video_picker)
        top_bar.add_widget(self.video_label)
        top_bar.add_widget(pick_btn)
        root.add_widget(top_bar)

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

        wm_row = BoxLayout(orientation="vertical", size_hint_y=None, height=110)
        wm_top = BoxLayout(size_hint_y=None, height=40)
        self.wm_cb = CheckBox(size_hint_x=None, width=40)
        wm_top.add_widget(self.wm_cb)
        wm_top.add_widget(Label(text="Наложить водяной знак (картинка)"))
        wm_row.add_widget(wm_top)

        wm_pick_btn = Button(text="Выбрать картинку", size_hint_y=None, height=35)
        wm_pick_btn.bind(on_release=self.open_image_picker)
        wm_row.add_widget(wm_pick_btn)

        self.wm_slider = Slider(min=10, max=90, value=60, size_hint_y=None,
                                height=30, disabled=True)
        self.wm_cb.bind(active=lambda w, val: setattr(self.wm_slider, "disabled", not val))
        wm_row.add_widget(self.wm_slider)
        grid.add_widget(wm_row)

        scroll.add_widget(grid)
        root.add_widget(scroll)

        self.status_label = Label(text="", size_hint_y=None, height=30)
        root.add_widget(self.status_label)

        process_btn = Button(text="Обработать видео", size_hint_y=None, height=55)
        process_btn.bind(on_release=self.start_processing)
        root.add_widget(process_btn)

        if ON_ANDROID:
            # Права на хранилище НЕ нужны (SAF + MediaStore). Старые
            # Android (<10) просим по-старому — там ещё работает.
            if request_permissions is not None and sdk_int() < 29:
                request_permissions([
                    Permission.READ_EXTERNAL_STORAGE,
                    Permission.WRITE_EXTERNAL_STORAGE,
                ])
            if bind_on_activity_result is not None:
                bind_on_activity_result(on_activity_result=self._on_activity_result)

        return root

    # -----------------------------------------------------------------
    # Выбор файлов
    # -----------------------------------------------------------------
    def open_video_picker(self, *_):
        if ON_ANDROID:
            self._start_get_content("video/*", REQ_PICK_VIDEO, "Выбери видео")
        else:
            self._show_chooser(self._on_video_chosen, "Movies", "DCIM")

    def open_image_picker(self, *_):
        if ON_ANDROID:
            self._start_get_content("image/*", REQ_PICK_IMAGE, "Выбери картинку")
        else:
            self._show_chooser(self._on_watermark_chosen, "Pictures", "DCIM")

    def _start_get_content(self, mime, req_code, title):
        Intent = autoclass("android.content.Intent")
        intent = Intent(Intent.ACTION_GET_CONTENT)
        intent.setType(mime)
        intent.addCategory(Intent.CATEGORY_OPENABLE)
        chooser = Intent.createChooser(intent, title)
        android_context().startActivityForResult(chooser, req_code)

    def _on_activity_result(self, request_code, result_code, intent):
        if result_code != RESULT_OK or intent is None:
            return
        uri = intent.getData()
        if uri is None:
            return
        if request_code == REQ_PICK_VIDEO:
            self._video_uri = uri
            name = self._query_display_name(uri) or "видео выбрано"
            self.video_label.text = name
        elif request_code == REQ_PICK_IMAGE:
            self._image_uri = uri
            self.wm_cb.active = True
            self.status_label.text = "Картинка для водяного знака выбрана"

    def _query_display_name(self, uri):
        try:
            resolver = android_context().getContentResolver()
            cursor = resolver.query(uri, None, None, None, None)
            if cursor is None:
                return None
            try:
                if cursor.moveToFirst():
                    idx = cursor.getColumnIndex("_display_name")
                    if idx >= 0:
                        return cursor.getString(idx)
            finally:
                cursor.close()
        except Exception:
            return None
        return None

    def _copy_uri_to_file(self, uri, dst_path):
        resolver = android_context().getContentResolver()
        pfd = resolver.openFileDescriptor(uri, "r")
        try:
            fd = pfd.getFd()
            with open(dst_path, "wb") as d:
                while True:
                    chunk = os.read(fd, 1024 * 1024)
                    if not chunk:
                        break
                    d.write(chunk)
        finally:
            try:
                pfd.close()
            except Exception:
                pass

    # --- десктопный выборщик (для локальных тестов) ---
    def _show_chooser(self, on_choose, *preferred_subdirs):
        base = os.path.expanduser("~")
        start_path = base
        for sub in preferred_subdirs:
            candidate = os.path.join(base, sub)
            if os.path.isdir(candidate):
                start_path = candidate
                break
        chooser = FileChooserIconView(path=start_path)
        btn_box = BoxLayout(size_hint_y=None, height=45)
        ok_btn = Button(text="Выбрать")
        btn_box.add_widget(ok_btn)
        wrapper = BoxLayout(orientation="vertical")
        wrapper.add_widget(chooser)
        wrapper.add_widget(btn_box)
        popup = Popup(title="Выбери файл", content=wrapper, size_hint=(0.95, 0.95))

        def choose(*_a):
            if chooser.selection:
                on_choose(chooser.selection[0])
            popup.dismiss()

        ok_btn.bind(on_release=choose)
        popup.open()

    def _on_video_chosen(self, path):
        self.selected_video = path
        self.video_label.text = os.path.basename(path)

    def _on_watermark_chosen(self, path):
        self.watermark_path = path

    # -----------------------------------------------------------------
    def collect_opts(self):
        opts = {}
        for key, (cb, slider) in self.row_widgets.items():
            if not cb.active:
                continue
            opts[key] = slider.value if slider is not None else True

        has_wm = (self._image_uri is not None) if ON_ANDROID else bool(self.watermark_path)
        if self.wm_cb.active and has_wm:
            opts["watermark_opacity"] = self.wm_slider.value
        return opts

    # -----------------------------------------------------------------
    def start_processing(self, *_):
        if ON_ANDROID and self._video_uri is None:
            self.status_label.text = "Сначала выбери видео!"
            return
        if not ON_ANDROID and not self.selected_video:
            self.status_label.text = "Сначала выбери видео!"
            return

        opts = self.collect_opts()
        if not opts:
            self.status_label.text = "Отметь хотя бы один эффект"
            return

        self.status_label.text = "Запускаю обработку..."
        threading.Thread(target=self._process_thread, args=(opts,), daemon=True).start()

    def _set_status(self, text):
        Clock.schedule_once(lambda dt: setattr(self.status_label, "text", text))

    def _process_thread(self, opts):
        try:
            if ON_ANDROID:
                ctx = android_context()
                cache = ctx.getCacheDir().getAbsolutePath()
                for name in os.listdir(cache):
                    if name.startswith("uq_"):
                        try:
                            os.remove(os.path.join(cache, name))
                        except OSError:
                            pass

                self._set_status("Копирую исходное видео...")
                input_path = os.path.join(cache, "uq_input.mp4")
                self._copy_uri_to_file(self._video_uri, input_path)

                watermark = None
                if self.wm_cb.active and self._image_uri is not None:
                    self._set_status("Копирую картинку...")
                    name = self._query_display_name(self._image_uri) or "w.png"
                    ext = os.path.splitext(name)[1].lower()
                    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"):
                        ext = ".png"
                    watermark = os.path.join(cache, "uq_watermark" + ext)
                    self._copy_uri_to_file(self._image_uri, watermark)

                if opts.get("trim_sec"):
                    self._set_status("Читаю длительность...")
                    opts["_source_duration"] = self._probe_duration(input_path)

                self._set_status("Обрабатываю ffmpeg... это может занять минуту")
                output_path = os.path.join(cache, "uq_output.mp4")
                command = build_ffmpeg_command(input_path, output_path, opts, watermark)
                self._run_ffmpegkit(command)
                if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
                    raise RuntimeError("FFmpeg не создал выходной файл")

                self._set_status("Сохраняю в Movies/Uniqualizer...")
                out_uri, out_public = self._publish_output(
                    output_path, "unique_%d.mp4" % int(time.time()))
                Clock.schedule_once(lambda dt: self._on_success(out_uri, out_public))
            else:
                input_path = self.selected_video
                out_dir = os.path.join(os.path.expanduser("~"), OUTPUT_DIR_NAME)
                os.makedirs(out_dir, exist_ok=True)
                output_path = os.path.join(out_dir, "unique_%d.mp4" % int(time.time()))
                if opts.get("trim_sec"):
                    opts["_source_duration"] = self._probe_duration(input_path)
                watermark = self.watermark_path if self.wm_cb.active else None
                command = build_ffmpeg_command(input_path, output_path, opts, watermark)
                import subprocess
                subprocess.run(["bash", "-lc", "ffmpeg " + command], check=True)
                Clock.schedule_once(lambda dt: self._on_success(None, output_path))
        except Exception as exc:
            Clock.schedule_once(lambda dt, e=exc: self._on_error(e))

    def _probe_duration(self, path):
        if ON_ANDROID:
            FFprobeKit = autoclass("com.arthenica.ffmpegkit.FFprobeKit")
            session = FFprobeKit.getMediaInformation(path)
            info = session.getMediaInformation() if session is not None else None
            duration = info.getDuration() if info is not None else None
            if duration:
                return float(duration)
            raise RuntimeError("Не удалось определить длительность видео")
        import subprocess
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path
        ])
        return float(out.decode().strip())

    def _run_ffmpegkit(self, command):
        FFmpegKit = autoclass("com.arthenica.ffmpegkit.FFmpegKit")
        ReturnCode = autoclass("com.arthenica.ffmpegkit.ReturnCode")
        session = FFmpegKit.execute(command)
        if not ReturnCode.isSuccess(session.getReturnCode()):
            logs = session.getFailStackTrace() or session.getOutput()
            raise RuntimeError("FFmpeg завершился с ошибкой:\n%s" % logs)

    def _publish_output(self, local_path, out_name):
        """Android 10+: через MediaStore -> файл появится в Movies/Uniqualizer.
        Android 9 и старше: прямая запись в Movies/Uniqualizer."""
        resolver = android_context().getContentResolver()
        if sdk_int() >= 29:
            ContentValues = autoclass("android.content.ContentValues")
            MediaStoreVideoMedia = autoclass("android.provider.MediaStore$Video$Media")
            values = ContentValues()
            values.put("_display_name", out_name)
            values.put("mime_type", "video/mp4")
            values.put("relative_path", "Movies/" + OUTPUT_DIR_NAME)
            uri = resolver.insert(MediaStoreVideoMedia.EXTERNAL_CONTENT_URI, values)
            if uri is None:
                raise RuntimeError("Не удалось создать файл в Movies/Uniqualizer")
            pfd = resolver.openFileDescriptor(uri, "rw")
            try:
                fd = pfd.getFd()
                with open(local_path, "rb") as src:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        os.write(fd, chunk)
            finally:
                pfd.close()
            return uri, None
        env = autoclass("android.os.Environment")
        base = env.getExternalStoragePublicDirectory(env.DIRECTORY_MOVIES).getAbsolutePath()
        dest_dir = os.path.join(base, OUTPUT_DIR_NAME)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, out_name)
        _copy_file(local_path, dest)
        return None, dest

    # -----------------------------------------------------------------
    def _on_success(self, out_uri, out_public):
        where = ("Movies/" + OUTPUT_DIR_NAME) if out_uri is not None else out_public
        self.status_label.text = "Готово: " + where

        content = BoxLayout(orientation="vertical", spacing=10, padding=10)
        content.add_widget(Label(text="Файл сохранён:\n" + where))
        btn_row = BoxLayout(size_hint_y=None, height=50, spacing=10)
        share_btn = Button(text="Поделиться")
        close_btn = Button(text="Закрыть")
        btn_row.add_widget(share_btn)
        btn_row.add_widget(close_btn)
        content.add_widget(btn_row)

        popup = Popup(title="Готово!", content=content, size_hint=(0.85, 0.4))
        close_btn.bind(on_release=popup.dismiss)

        def do_share(*_a):
            if out_uri is not None:
                self._share_uri(out_uri)
            elif share is not None and out_public:
                try:
                    share.share_file(out_public)
                except Exception:
                    pass

        share_btn.bind(on_release=do_share)
        popup.open()

    def _share_uri(self, uri):
        try:
            Intent = autoclass("android.content.Intent")
            send = Intent(Intent.ACTION_SEND)
            send.setType("video/mp4")
            send.putExtra(Intent.EXTRA_STREAM, uri)
            send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            chooser = Intent.createChooser(send, "Поделиться видео")
            chooser.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            android_context().startActivity(chooser)
        except Exception as exc:
            self._on_error(exc)

    def _on_error(self, exc):
        self.status_label.text = "Ошибка обработки"
        text = str(exc)
        if len(text) > 700:
            text = text[:700] + "..."
        popup = Popup(title="Ошибка", content=Label(text=text),
                      size_hint=(0.9, 0.6))
        popup.open()


if __name__ == "__main__":
    UniqualizerApp().run()
