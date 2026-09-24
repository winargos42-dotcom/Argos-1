#!/usr/bin/env bash
# ARGOS: установка Coral Edge TPU (PCIe/M.2, 1ac1:089a) и сервиса argos-vision-accel
# на узле argos-coral (Debian 12, ядро 6.1). Запускать на УЗЛЕ от root ПОСЛЕ установки
# системы на SSD. Повторный запуск безопасен.
#
#   ./install-coral-debian12.sh --check     только проверки, ничего не меняет (по умолчанию)
#   ./install-coral-debian12.sh --install   драйвер + libedgetpu + tflite + сервис
#
# Файлы рядом со скриптом: fetch-models.sh, argos-vision-accel.service, vision-accel.env.example,
# ../../argos_deploy/src/vision/coral_{protocol,accel_server}.py (или уже скопированные в APP).
set -euo pipefail

MODE="${1:---check}"
HERE="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/argos-vision/app
VENV=/opt/argos-vision/venv
MODELS=/var/lib/argos-vision/models
TFLITE_VERSION="${ARGOS_TFLITE_VERSION:-2.14.0}"   # последний tflite-runtime с колесом cp311
KREL="$(uname -r)"

say() { printf '== %s\n' "$*"; }
die() { printf '!! %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "нужен root"
. /etc/os-release
[ "${VERSION_CODENAME:-}" = bookworm ] || die "ожидался Debian 12 (bookworm), а это ${PRETTY_NAME:-?}"
grep -q ' / .*overlay\| / .*tmpfs' /proc/mounts && die "корень в RAM (Live) — ставь на систему с SSD"

say "Проверки"
lspci -nn | grep -q '1ac1:089a' || die "Coral PCIe (1ac1:089a) не найден в lspci"
lspci -nn | grep '1ac1:089a'
echo "ядро: $KREL"
if [ -e /dev/apex_0 ]; then echo "/dev/apex_0 уже есть — драйвер загружен"; fi
apt-cache policy "linux-headers-$KREL" | grep -q 'Candidate: [0-9]' \
  || die "нет пакета linux-headers-$KREL в APT: обнови ядро (apt full-upgrade, перезагрузка) и повтори"
echo "linux-headers-$KREL доступны"

if [ "$MODE" != --install ]; then
  say "Режим проверки: ничего не изменено. Для установки: $0 --install"
  exit 0
fi

say "Базовые пакеты и заголовки ТОЧНО для $KREL"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends "linux-headers-$KREL" dkms build-essential \
  curl gnupg ca-certificates python3-venv python3-numpy python3-pil pciutils

if dpkg-query -W -f='${Status}' gasket-dkms libedgetpu1-std 2>/dev/null | grep -c 'install ok installed' | grep -q 2; then
  say "gasket-dkms и libedgetpu1-std уже установлены (образ Codex) — репозиторий и драйвер не трогаю"
else
  say "Репозиторий Coral (ключ в отдельном keyring, только для этого источника)"
  if ! grep -qs coral-edgetpu-stable /etc/apt/sources.list.d/*.list; then
    KEYRING=/usr/share/keyrings/coral-edgetpu.gpg
    curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o "$KEYRING.tmp"
    mv "$KEYRING.tmp" "$KEYRING"
    echo "deb [signed-by=$KEYRING] https://packages.cloud.google.com/apt coral-edgetpu-stable main" \
      > /etc/apt/sources.list.d/coral-edgetpu.list
    apt-get update
  fi
  say "Драйвер gasket/apex (DKMS) и libedgetpu1-std"
  if ! apt-get install -y gasket-dkms libedgetpu1-std; then
    dkms status || true
    die "gasket-dkms не собрался под $KREL. Не подбирай ядро наугад: собери DKMS-пакет из
официального github.com/google/gasket-driver на ЗАКРЕПЛЁННОМ коммите (dpkg-buildpackage -us -uc -tc -b)
и установи его, затем повтори этот скрипт."
  fi
fi
modinfo -k "$KREL" apex >/dev/null || die "модуль apex не собран для $KREL (dkms status)"

say "Доступ к /dev/apex_0 только группе apex"
getent group apex >/dev/null || groupadd --system apex
[ -f /etc/udev/rules.d/65-apex.rules ] || echo 'SUBSYSTEM=="apex", MODE="0660", GROUP="apex"' > /etc/udev/rules.d/65-apex.rules
udevadm control --reload-rules
modprobe gasket; modprobe apex
udevadm trigger --subsystem-match=apex || true
sleep 1
[ -e /dev/apex_0 ] || die "/dev/apex_0 не появился (dmesg | grep -i apex)"
ls -l /dev/apex_0

say "Пользователь сервиса и каталоги"
id argos-vision >/dev/null 2>&1 || useradd --system --home /opt/argos-vision --shell /usr/sbin/nologin argos-vision
usermod -aG apex argos-vision
install -d -m 755 /opt/argos-vision "$APP"
install -d -m 750 -o argos-vision -g argos-vision /var/lib/argos-vision "$MODELS"
install -d -m 750 /etc/argos

say "libedgetpu: согласованная сборка feranick (стоковый 16.0 2021 несовместим с новым рантаймом)"
# CPU без AVX2 (Sandy Bridge) не тянет колёса, собранные с AVX2, поэтому рантайм берём с PyPI (только AVX),
# а libedgetpu — feranick под ту же линейку TF (ABI совместим с tflite 2.14).
EDGE_DEB_URL="${ARGOS_LIBEDGETPU_URL:-https://github.com/feranick/libedgetpu/releases/download/v16.0TF2.15.1-1/libedgetpu1-std_16.0tf2.15.1-1.bookworm_amd64.deb}"
if ! dpkg -s libedgetpu1-std 2>/dev/null | grep -q 'Version: 16.0tf'; then
  tmp_deb="$(mktemp --suffix=.deb)"
  curl -fsSL --retry 3 -o "$tmp_deb" "$EDGE_DEB_URL" || die "не скачался libedgetpu feranick"
  dpkg -i "$tmp_deb" || apt-get -y -f install
  rm -f "$tmp_deb"
fi
dpkg -s libedgetpu1-std | grep -i version

say "Python: venv с системными numpy/Pillow + PyPI tflite-runtime $TFLITE_VERSION (AVX-only)"
[ -x "$VENV/bin/python" ] || python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install "tflite-runtime==$TFLITE_VERSION"
"$VENV/bin/python" -c "import numpy" 2>/dev/null || "$VENV/bin/pip" install numpy Pillow
# Проверка, что рантайм вообще запускается на этом CPU (Illegal instruction = колесо с AVX2)
"$VENV/bin/python" -c "from tflite_runtime.interpreter import Interpreter; print('tflite-runtime OK на этом CPU')" \
  || die "tflite-runtime падает на этом CPU (нужно колесо без AVX2 — используй PyPI, не feranick-сборку)"

say "Код сервиса"
SRC="$HERE/../../argos_deploy/src/vision"
for f in coral_protocol.py coral_accel_server.py; do
  if [ -f "$SRC/$f" ]; then install -m 644 "$SRC/$f" "$APP/$f"
  elif [ -f "$HERE/$f" ]; then install -m 644 "$HERE/$f" "$APP/$f"
  else die "нет $f"; fi
done

say "Модели"
ARGOS_CORAL_MODELS_DIR="$MODELS" bash "$HERE/fetch-models.sh"
chown -R argos-vision:argos-vision "$MODELS"

say "Ключ и настройки"
if [ ! -f /etc/argos/coral.key ]; then
  echo "!! Нет /etc/argos/coral.key. Скопируй ТОТ ЖЕ ключ, что на X230 (/etc/argos/coral.key), затем:"
  echo "   chown argos-vision:argos-vision /etc/argos/coral.key && chmod 600 /etc/argos/coral.key"
fi
[ -f /etc/argos/vision-accel.env ] || install -m 640 -g argos-vision "$HERE/vision-accel.env.example" /etc/argos/vision-accel.env
install -m 644 "$HERE/argos-vision-accel.service" /etc/systemd/system/argos-vision-accel.service
systemctl daemon-reload

say "Бенчмарк на Edge TPU (от имени сервиса)"
runuser -u argos-vision -- env ARGOS_CORAL_MODELS_DIR="$MODELS" \
  "$VENV/bin/python" "$APP/coral_accel_server.py" --benchmark 50 --model objects \
  || die "бенчмарк не прошёл — смотри ошибку выше (часто: несовместимость libedgetpu и tflite-runtime)"

say "Готово. Проверь /etc/argos/vision-accel.env и ключ, затем: systemctl enable --now argos-vision-accel"
