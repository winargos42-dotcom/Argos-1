#!/usr/bin/env bash
# ARGOS: ночной бэкап незаменимых данных X230 на диск узла argos-coral.
# Снимки по дням с жёсткими ссылками (--link-dest): каждый снимок выглядит как полная
# копия, но неизменные файлы не занимают места повторно. Секреты /etc/argos шифруются
# (openssl AES-256) ключом, который лежит ТОЛЬКО на X230 — узел хранит их зашифрованными.
#
# Восстановление одного файла:
#   rsync -e "ssh -i КЛЮЧ ..." root@УЗЕЛ:/var/backups/x230/current/state/mempalace/ФАЙЛ .
# Восстановление секретов:
#   ssh ... root@УЗЕЛ "cat /var/backups/x230/current/etc-argos.tar.gz.enc" \
#     | openssl enc -d -aes-256-cbc -pbkdf2 -pass file:/etc/argos/backup.pass | tar -C /tmp -xzf -
set -euo pipefail

NODE="${ARGOS_BACKUP_NODE:-root@192.168.1.94}"
KEY="${ARGOS_BACKUP_KEY:-/root/argos-runtime/work/pxe-coral-20260924/private/argos-coral-ed25519}"
HK="${ARGOS_BACKUP_KNOWNHOSTS:-/root/argos-runtime/work/pxe-coral-20260924/private/known_hosts-ssd}"
DEST="${ARGOS_BACKUP_DEST:-/var/backups/x230}"
PASS="${ARGOS_BACKUP_PASS:-/etc/argos/backup.pass}"
KEEP="${ARGOS_BACKUP_KEEP:-7}"
SSHOPT=(-i "$KEY" -o "UserKnownHostsFile=$HK" -o StrictHostKeyChecking=yes -o ConnectTimeout=20 -o BatchMode=yes)

SOURCES=(
  /root/argos-runtime/checks
  /root/argos-runtime/state
  /home/SiG/Projects
  /home/SiG/Obsidian
)

log() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }

# Ключ шифрования секретов — только на X230, генерируется один раз
if [ ! -s "$PASS" ]; then
  ( umask 077; head -c 32 /dev/urandom | base64 > "$PASS" )
  log "создан ключ шифрования бэкапа $PASS (храни его — без него секреты не восстановить)"
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
# Зашифрованный архив секретов
tar -C /etc -czf - argos | openssl enc -aes-256-cbc -pbkdf2 -salt -pass "file:$PASS" -out "$TMP/etc-argos.tar.gz.enc"

DATE="$(date +%F)"
SNAP="$DEST/$DATE"
ssh "${SSHOPT[@]}" "$NODE" "mkdir -p '$DEST'"

# Существующие источники + зашифрованные секреты; --link-dest на прошлый снимок
present=()
for s in "${SOURCES[@]}"; do [ -e "$s" ] && present+=("$s"); done
log "снимок $SNAP: ${#present[@]} источников + секреты"
rsync -az --delete --link-dest="$DEST/current" -e "ssh ${SSHOPT[*]}" \
  "${present[@]}" "$TMP/etc-argos.tar.gz.enc" "$NODE:$SNAP/"

# Обновить указатель current и почистить старые снимки
ssh "${SSHOPT[@]}" "$NODE" \
  "rm -f '$DEST/current'; ln -s '$SNAP' '$DEST/current'; \
   ls -1d '$DEST'/20* 2>/dev/null | sort | head -n -$KEEP | xargs -r rm -rf; \
   du -sh '$SNAP' | cut -f1"
log "готово: $(ssh "${SSHOPT[@]}" "$NODE" "ls -1d '$DEST'/20* | wc -l") снимков на узле"
