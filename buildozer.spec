[app]

# Application metadata
title = ARGOS Universal OS
package.name = argos_universal
package.domain = org.iliyaqdrwalqu.argos
version = 2.1.3

# Source
source.dir = .
source.main = main_kivy.py
source.include_exts = py,png,jpg,jpeg,kv,atlas,json,txt,md,xml
source.include_patterns = assets/*,config/*,res/*

# Requirements
requirements = hostpython3,python3,kivy==2.3.1,requests,pyjnius==1.6.1,android,paho-mqtt,python-dotenv

# Hook script – patches pyjnius for Python 3 and disables Android-incompatible
# Python stdlib C extensions (grp, _uuid, _lzma) before the build starts.
p4a.hook = p4a_hook.py

# Local recipes directory – contains a custom pyjnius recipe that replaces
# the Python-2-only ``long`` built-in with ``int`` so Cython 3.x can compile
# pyjnius without raising "undeclared name not builtin: long".
p4a.local_recipes = p4a-recipes

# Orientation
orientation = portrait
fullscreen = 0

# Android permissions
android.permissions = INTERNET,BLUETOOTH_ADMIN,NFC,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,USB_HOST,ACCESS_NETWORK_STATE,FOREGROUND_SERVICE,REQUEST_INSTALL_PACKAGES

# Android API / NDK / SDK
android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a

# [FIX-SAI-FILEPROVIDER]
# FileProvider is injected into AndroidManifest.xml by the p4a_hook.py
# after_apk_build hook, which places <provider> correctly inside <application>.
# The res/xml/file_paths.xml resource (required by the provider) is included
# via android.add_src below.
android.add_src = res

# Enable Android features
android.accept_sdk_license = True

# Icons
icon.filename = %(source.dir)s/assets/argos_icon_512.png

[buildozer]
log_level = 2
warn_on_root = 0
