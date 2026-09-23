#!/usr/bin/env bash
# ARGOS workstation setup for the Lenovo ThinkPad X230 (Arch Linux).
#
# Run this ON THE PHYSICAL X230, as root, NOT in a CI/cloud sandbox.
# It is meant to be run block by block (each numbered section below is
# self-contained) rather than executed blindly start to finish — several
# steps prompt for confirmation (pacman -Syu) or assume prior steps
# succeeded (Docker storage migration depends on the docker package
# being installed first, etc). Read a block before running it.
#
# X230 drivers already work correctly out of the box and are not touched
# here: Intel HD 4000 -> i915, network -> e1000e, audio -> snd_hda_intel,
# Wi-Fi -> iwlwifi.
#
# Layout assumed:
#   sda2 - system root (/)
#   sda3 - bulk storage, mounted so that /home/.argos-storage and
#          /home/.arch-storage live on it
#
# Vivado is intentionally NOT installed here; it is placed later at
# /home/.tools/Xilinx.
#
# The VMess UUID used with v2rayA prior to this setup should be
# considered disclosed and rotated with the server owner once this
# script has been run.

set -u

# ---------------------------------------------------------------------
# 1. Fix Ollama model path (point at storage moved to sda3)
# ---------------------------------------------------------------------
step_01_ollama_storage() {
    local STORE="/home/.argos-storage/ollama-models"

    if [ ! -d "$STORE" ]; then
        echo "ОШИБКА: не найден каталог $STORE"
        return 1
    fi

    local OLLAMA_USER OLLAMA_GROUP
    OLLAMA_USER="$(systemctl show ollama.service -p User --value)"
    [ -n "$OLLAMA_USER" ] || OLLAMA_USER=root
    OLLAMA_GROUP="$(id -gn "$OLLAMA_USER")"

    echo "Ollama работает от: $OLLAMA_USER:$OLLAMA_GROUP"

    chown -R "$OLLAMA_USER:$OLLAMA_GROUP" "$STORE"
    chmod 755 /home/.argos-storage
    chmod 755 "$STORE"

    mkdir -p /etc/systemd/system/ollama.service.d
    cat > /etc/systemd/system/ollama.service.d/10-storage.conf <<'EOF'
[Service]
Environment="OLLAMA_MODELS=/home/.argos-storage/ollama-models"
EOF

    systemctl daemon-reload
    systemctl restart ollama.service
    sleep 3

    systemctl show ollama.service -p User -p Environment --no-pager
    ollama list
}

step_01_verify_storage() {
    du -sh /home/.argos-storage/ollama-models
    find /home/.argos-storage/ollama-models/manifests -type f 2>/dev/null | head -20
    # If `ollama list` is empty but blobs exist: the download's blocks
    # transferred but the manifest never got written (no completed pull).
    # Do NOT re-download a large model to "fix" this until confirmed.
}

# ---------------------------------------------------------------------
# 2. Time sync, proxy (v2rayA), and periodic maintenance
# ---------------------------------------------------------------------
step_02_time_proxy_maintenance() {
    timedatectl set-ntp true

    systemctl enable --now systemd-timesyncd.service
    systemctl enable --now NetworkManager.service
    systemctl enable --now v2raya.service
    systemctl enable --now fstrim.timer

    systemctl restart systemd-timesyncd.service
    systemctl restart v2raya.service

    echo "Синхронизация времени:"
    timedatectl show -p NTPSynchronized -p Timezone -p LocalRTC

    echo
    echo "Службы:"
    systemctl is-active systemd-timesyncd.service NetworkManager.service v2raya.service

    echo
    echo "Порты v2rayA:"
    ss -lntp | grep -E ':2017|:20170|:20171|:20172' || true

    # Expected: NTPSynchronized=yes / active / active / active
}

# ---------------------------------------------------------------------
# 3. Move pacman cache + journal limits to sda3
# ---------------------------------------------------------------------
step_03_pacman_cache_and_journal() {
    if pgrep -x pacman >/dev/null || pgrep -x makepkg >/dev/null \
        || pgrep -x yay >/dev/null || pgrep -x paru >/dev/null; then
        echo "СТОП: работает пакетный менеджер:"
        ps -eo pid,comm,args | grep -E 'pacman|makepkg|yay|paru' | grep -v grep
        return 1
    fi

    rm -f /var/lib/pacman/db.lck

    local CACHE="/home/.arch-storage/pacman-pkg"
    install -d -m 0755 "$CACHE"
    cp -a /var/cache/pacman/pkg/. "$CACHE"/ 2>/dev/null || true

    if ! pacman-conf CacheDir | grep -Fxq "$CACHE/"; then
        cp -a /etc/pacman.conf "/etc/pacman.conf.backup-$(date +%F-%H%M%S)"
        sed -i '/^[[:space:]]*CacheDir[[:space:]]*=/d' /etc/pacman.conf
        sed -i "/^\[options\]$/a CacheDir = $CACHE/" /etc/pacman.conf
    fi

    echo "Настроенные каталоги кэша:"
    pacman-conf CacheDir

    if pacman-conf CacheDir | grep -Fxq "$CACHE/"; then
        find /var/cache/pacman/pkg -mindepth 1 -maxdepth 1 -delete
        echo "Кэш Pacman перенесён на sda3."
    else
        echo "ОШИБКА: новый CacheDir не применился."
    fi

    df -h / /home

    mkdir -p /etc/systemd/journald.conf.d
    cat > /etc/systemd/journald.conf.d/10-size.conf <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=300M
RuntimeMaxUse=100M
MaxRetentionSec=14day
EOF

    systemctl restart systemd-journald.service
    journalctl --vacuum-size=300M

    df -h /
}

# ---------------------------------------------------------------------
# 4. Full Arch update
# ---------------------------------------------------------------------
step_04_full_update() {
    if pgrep -x pacman >/dev/null; then
        echo "Pacman уже работает — дождись окончания."
        return 1
    fi

    rm -f /var/lib/pacman/db.lck
    pacman -Syu
    # At "Приступить к установке? [Y/n]" answer: y

    df -h / /home
    systemctl --failed
    # fwupdmgr update is intentionally NOT re-run here.
}

# ---------------------------------------------------------------------
# 5. install-repo helper: skip packages missing from enabled repos
#    instead of aborting the whole install
# ---------------------------------------------------------------------
step_05_install_install_repo_helper() {
    cat > /usr/local/sbin/install-repo <<'EOF'
#!/usr/bin/env bash

set -u

AVAILABLE=()
MISSING=()

for PACKAGE in "$@"; do
    if pacman -Si "$PACKAGE" >/dev/null 2>&1; then
        AVAILABLE+=("$PACKAGE")
    else
        MISSING+=("$PACKAGE")
    fi
done

if ((${#AVAILABLE[@]})); then
    echo
    echo "Будут установлены:"
    printf '  %s\n' "${AVAILABLE[@]}"

    pacman -S --needed "${AVAILABLE[@]}"
fi

if ((${#MISSING[@]})); then
    echo
    echo "Не найдены в официальных репозиториях:"
    printf '  %s\n' "${MISSING[@]}"
fi
EOF

    chmod 755 /usr/local/sbin/install-repo
}

# ---------------------------------------------------------------------
# 6. Base engineering toolset + mirror/cache maintenance
# ---------------------------------------------------------------------
step_06_base_engineering_toolset() {
    install-repo \
        pacman-contrib reflector base-devel git git-lfs github-cli \
        openssh curl wget aria2 jq go-yq rsync rclone restic 7zip \
        unzip zip tree ripgrep fd fzf bat eza tmux screen htop btop \
        ncdu lsof strace bash-completion zoxide fastfetch \
        smartmontools ethtool iw

    systemctl enable --now paccache.timer 2>/dev/null || true

    if command -v reflector >/dev/null 2>&1; then
        mkdir -p /etc/xdg/reflector
        cat > /etc/xdg/reflector/reflector.conf <<'EOF'
--save /etc/pacman.d/mirrorlist
--protocol https
--latest 20
--sort rate
EOF
        systemctl enable --now reflector.timer
    fi

    git --version
    gh --version
    rclone version | head -1
    restic version
    fastfetch
    df -h /
}

# ---------------------------------------------------------------------
# 7. Python, Node.js, Java, build tooling + per-user project dirs
# ---------------------------------------------------------------------
step_07_dev_languages_and_user_dirs() {
    install-repo \
        python python-pip python-pipx python-virtualenv \
        python-setuptools python-wheel uv nodejs npm pnpm go rustup \
        cmake ninja meson ccache sqlite shellcheck shfmt \
        jdk17-openjdk code

    if archlinux-java status | grep -q 'java-17-openjdk'; then
        archlinux-java set java-17-openjdk
    fi
    java -version

    MAIN_USER="$(
        awk -F: '
            $3 >= 1000 &&
            $3 < 60000 &&
            $6 ~ /^\/home\// &&
            $7 !~ /(nologin|false)$/ {
                print $1
                exit
            }
        ' /etc/passwd
    )"
    MAIN_HOME="$(getent passwd "$MAIN_USER" | cut -d: -f6)"

    echo "Пользователь: $MAIN_USER"
    echo "Домашний каталог: $MAIN_HOME"

    install -d -o "$MAIN_USER" -g "$MAIN_USER" \
        "$MAIN_HOME/Projects" \
        "$MAIN_HOME/venvs" \
        "$MAIN_HOME/bin" \
        "$MAIN_HOME/system-state" \
        "$MAIN_HOME/VirtualBox VMs"

    runuser -l "$MAIN_USER" -c '
        git lfs install
        pipx ensurepath

        if command -v rustup >/dev/null 2>&1; then
            rustup default stable
        fi
    '
}

# ---------------------------------------------------------------------
# 8. Docker, fully relocated to sda3
# ---------------------------------------------------------------------
step_08_docker_on_sda3() {
    : "${MAIN_USER:?run step_07_dev_languages_and_user_dirs first, or set MAIN_USER manually}"

    install-repo docker docker-compose docker-buildx

    systemctl stop docker.service docker.socket 2>/dev/null || true

    local DOCKER_STORE="/home/.arch-storage/docker"
    install -d -o root -g root -m 0711 "$DOCKER_STORE"

    if [ -d /var/lib/docker ]; then
        rsync -aHAX --numeric-ids /var/lib/docker/ "$DOCKER_STORE"/
    fi

    mkdir -p /etc/docker
    [ -f /etc/docker/daemon.json ] &&
        cp -a /etc/docker/daemon.json \
            "/etc/docker/daemon.json.backup-$(date +%F-%H%M%S)"

    python - <<'PY'
import json
from pathlib import Path

path = Path("/etc/docker/daemon.json")
data = {}

if path.exists() and path.read_text().strip():
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Ошибка в /etc/docker/daemon.json: {exc}")

data["data-root"] = "/home/.arch-storage/docker"

path.write_text(
    json.dumps(data, indent=2, ensure_ascii=False) + "\n"
)
PY

    cat /etc/docker/daemon.json

    systemctl daemon-reload
    systemctl enable --now docker.service
    sleep 3

    docker info --format 'Docker Root Dir: {{.DockerRootDir}}'

    if [ "$(docker info --format '{{.DockerRootDir}}')" = "/home/.arch-storage/docker" ]; then
        find /var/lib/docker -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
        echo "Старая копия Docker удалена с sda2."
    fi

    getent group docker >/dev/null && usermod -aG docker "$MAIN_USER"

    docker compose version
    docker buildx version
    df -h / /home

    # Note: the `docker` group is effectively root-equivalent; only the
    # primary user is added to it, deliberately.
}

# ---------------------------------------------------------------------
# 9. Valkey (Redis-compatible cache) for ARGOS
# ---------------------------------------------------------------------
step_09_valkey() {
    install-repo valkey

    if systemctl list-unit-files | grep -q '^valkey.service'; then
        systemctl enable --now valkey.service
    fi

    redis-cli ping 2>/dev/null || valkey-cli ping 2>/dev/null || true
    # Expected: PONG
}

# ---------------------------------------------------------------------
# 10. VirtualBox
#     Fully shut down any running Windows VM before running this.
# ---------------------------------------------------------------------
step_10_virtualbox() {
    : "${MAIN_USER:?run step_07_dev_languages_and_user_dirs first, or set MAIN_USER manually}"

    install-repo \
        virtualbox virtualbox-host-dkms linux-headers dkms \
        virtualbox-guest-iso

    dkms autoinstall
    modprobe vboxdrv

    getent group vboxusers >/dev/null && usermod -aG vboxusers "$MAIN_USER"

    runuser -l "$MAIN_USER" -c '
        mkdir -p "$HOME/VirtualBox VMs"
        VBoxManage setproperty machinefolder "$HOME/VirtualBox VMs"
    '

    VBoxManage --version
    lsmod | grep vbox
}

# ---------------------------------------------------------------------
# 11. Android, phone/camera, remote-access tooling
# ---------------------------------------------------------------------
step_11_android_and_remote_access() {
    : "${MAIN_USER:?run step_07_dev_languages_and_user_dirs first, or set MAIN_USER manually}"

    install-repo \
        android-tools android-udev scrcpy ffmpeg v4l-utils \
        python-opencv imagemagick mpv remmina freerdp gtk-vnc \
        mosquitto avahi nss-mdns syncthing keepassxc

    for GROUP in adbusers uucp lock wireshark; do
        if getent group "$GROUP" >/dev/null; then
            usermod -aG "$GROUP" "$MAIN_USER"
            echo "$MAIN_USER добавлен в $GROUP"
        fi
    done

    udevadm control --reload-rules
    udevadm trigger

    systemctl enable --now avahi-daemon.service

    adb version
    adb devices -l
    scrcpy --version
    ffmpeg -version | head -1

    python - <<'PY'
import cv2
print("OpenCV:", cv2.__version__)
PY

    command -v remmina
}

# ---------------------------------------------------------------------
# 12. ARGOS Python environment on sda3
# ---------------------------------------------------------------------
step_12_argos_python_env() {
    : "${MAIN_USER:?run step_07_dev_languages_and_user_dirs first, or set MAIN_USER manually}"

    runuser -l "$MAIN_USER" -c '
        python -m venv --system-site-packages "$HOME/venvs/argos-tools"

        "$HOME/venvs/argos-tools/bin/python" -m pip install \
            --upgrade pip setuptools wheel

        "$HOME/venvs/argos-tools/bin/pip" install \
            fastapi "uvicorn[standard]" httpx pydantic python-dotenv \
            redis paho-mqtt psutil websockets pillow
    '

    runuser -l "$MAIN_USER" -c '
        "$HOME/venvs/argos-tools/bin/python" -c "
import fastapi
import httpx
import redis
import paho.mqtt.client
import psutil
import cv2

print(\"FastAPI:\", fastapi.__version__)
print(\"OpenCV:\", cv2.__version__)
print(\"ARGOS Python environment: OK\")
"
    '
}

# ---------------------------------------------------------------------
# 13. ESP32 / JTAG / FPGA toolchain
#     Only run if `df -h /` shows at least 5 GB free.
# ---------------------------------------------------------------------
step_13_esp32_jtag_fpga() {
    df -h /

    install-repo \
        platformio-core platformio-core-udev esptool openocd \
        openfpgaloader flashrom avrdude dfu-util picocom minicom \
        iverilog verilator gtkwave yosys sigrok-cli pulseview

    udevadm control --reload-rules
    udevadm trigger

    pio --version 2>/dev/null || true
    esptool version 2>/dev/null || true
    openocd --version
    openFPGALoader --version 2>/dev/null || true
    iverilog -V | head -2
    verilator --version
    yosys -V

    # Vivado is intentionally not installed here; it will live at
    # /home/.tools/Xilinx once placed manually.
}

# ---------------------------------------------------------------------
# 14. Power management (TLP) for the X230
#     TLP and power-profiles-daemon must not run at the same time.
# ---------------------------------------------------------------------
step_14_power_management() {
    if pacman -Q tlp >/dev/null 2>&1; then
        systemctl disable --now power-profiles-daemon.service 2>/dev/null || true

        systemctl enable --now tlp.service
        tlp start

        tlp-stat -s
        tlp-stat -b
    fi

    # Fan control via thinkfan is intentionally left alone — the X230's
    # stock fan management already works.
}

# ---------------------------------------------------------------------
# 15. System health-check command
# ---------------------------------------------------------------------
step_15_install_health_check() {
    cat > /usr/local/bin/argos-health <<'EOF'
#!/usr/bin/env bash

echo "========== СИСТЕМА =========="
date
uname -a
uptime

echo
echo "========== ДИСКИ =========="
df -h / /home

echo
echo "========== ПАМЯТЬ =========="
free -h
swapon --show

echo
echo "========== ОШИБОЧНЫЕ СЛУЖБЫ =========="
systemctl --failed --no-pager

echo
echo "========== ОСНОВНЫЕ СЛУЖБЫ =========="
for SERVICE in \
    NetworkManager \
    systemd-timesyncd \
    v2raya \
    ollama \
    docker \
    tlp
do
    printf "%-22s " "$SERVICE"
    systemctl is-active "$SERVICE" 2>/dev/null || true
done

echo
echo "========== ПРОКСИ =========="
ss -lntp 2>/dev/null | grep -E ':2017|:20170|:20171|:20172' || true

echo
echo "========== OLLAMA =========="
ollama list 2>/dev/null || true

echo
echo "========== DOCKER =========="
docker info --format 'Root={{.DockerRootDir}}' 2>/dev/null || true
docker ps 2>/dev/null || true

echo
echo "========== ТЕМПЕРАТУРЫ =========="
sensors 2>/dev/null || true
EOF

    chmod 755 /usr/local/bin/argos-health
    argos-health
}

# ---------------------------------------------------------------------
# Finish: snapshot installed packages, final checks, reboot
# ---------------------------------------------------------------------
step_finish() {
    : "${MAIN_USER:?run step_07_dev_languages_and_user_dirs first, or set MAIN_USER manually}"

    runuser -l "$MAIN_USER" -c '
        pacman -Qqe > "$HOME/system-state/pacman-explicit-$(date +%F).txt"
        pacman -Qqm > "$HOME/system-state/foreign-packages-$(date +%F).txt"
    '

    df -h / /home
    systemctl --failed
    systemctl is-active v2raya ollama docker
    groups "$MAIN_USER"

    echo
    echo "Готово к перезагрузке. Выполни: reboot"
    echo "После загрузки под обычным пользователем проверь:"
    echo "  argos-health"
    echo "  VBoxManage --version"
    echo "  adb devices -l"
    echo "  docker ps"
    echo "  ollama list"
    echo
    echo "Также замени у владельца сервера ранее использованный VMess UUID — он считается раскрытым."
}

# ---------------------------------------------------------------------
# Entry point: run all steps in order, or a single named step, e.g.
#   ./workstation-setup-x230.sh step_01_ollama_storage
#   ./workstation-setup-x230.sh            # runs everything, in order
# ---------------------------------------------------------------------
main() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "Запусти от root." >&2
        exit 1
    fi

    if [ "$#" -gt 0 ]; then
        "$@"
        return
    fi

    step_01_ollama_storage
    step_01_verify_storage
    step_02_time_proxy_maintenance
    step_03_pacman_cache_and_journal
    step_04_full_update
    step_05_install_install_repo_helper
    step_06_base_engineering_toolset
    step_07_dev_languages_and_user_dirs
    step_08_docker_on_sda3
    step_09_valkey
    step_10_virtualbox
    step_11_android_and_remote_access
    step_12_argos_python_env
    step_13_esp32_jtag_fpga
    step_14_power_management
    step_15_install_health_check
    step_finish
}

main "$@"
