[app]

title = Uniqualizer
package.name = uniqualizer
package.domain = org.uniqualizer

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 0.1

# plyer нужен для "Поделиться", pyjnius — для вызова FFmpegKit (Java-библиотека)
requirements = python3,kivy==2.3.0,plyer,pyjnius

orientation = portrait
fullscreen = 0

# Если захочешь свою иконку — положи icon.png (квадратный, 512x512) в папку
# проекта и раскомментируй строку ниже.
# icon.filename = %(source.dir)s/icon.png

# ---------------------------------------------------------------------
# Разрешения. MANAGE_EXTERNAL_STORAGE нужен на Android 11+, чтобы
# приложение могло свободно читать/писать видео в Movies/DCIM.
# ---------------------------------------------------------------------
android.permissions = READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE,READ_MEDIA_VIDEO,READ_MEDIA_IMAGES

android.api = 33
android.minapi = 23
android.ndk = 25b
android.enable_androidx = True

# Подключаем готовую Android-библиотеку FFmpegKit через Gradle —
# именно она умеет выполнять ffmpeg-команды внутри приложения.
android.gradle_dependencies = com.arthenica:ffmpeg-kit-full:6.0-2

# Собираем только под 64-битные устройства — так значительно быстрее
# идёт первая сборка. Если нужен armeabi-v7a — добавь через запятую.
android.archs = arm64-v8a

android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
