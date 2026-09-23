#!/usr/bin/env bash
# ARGOS: модели для Coral из официального github.com/google-coral/test_data.
# При первой загрузке пишет SHA256SUMS (доверие при первом использовании),
# при следующих — сверяет и отказывается работать с изменёнными файлами.
set -euo pipefail
DIR="${ARGOS_CORAL_MODELS_DIR:-/var/lib/argos-vision/models}"
BASE=https://raw.githubusercontent.com/google-coral/test_data/master
FILES=(
  ssd_mobilenet_v2_coco_quant_postprocess_edgetpu.tflite
  ssd_mobilenet_v2_coco_quant_postprocess.tflite
  coco_labels.txt
  ssd_mobilenet_v2_face_quant_postprocess_edgetpu.tflite
  ssd_mobilenet_v2_face_quant_postprocess.tflite
)
mkdir -p "$DIR"
cd "$DIR"
for f in "${FILES[@]}"; do
  [ -s "$f" ] && continue
  echo "загружаю $f"
  curl -fsSL --retry 3 -o "$f.part" "$BASE/$f"
  mv "$f.part" "$f"
done
if [ -f SHA256SUMS ]; then
  sha256sum -c --quiet SHA256SUMS && echo "модели сверены с SHA256SUMS"
else
  sha256sum "${FILES[@]}" > SHA256SUMS
  echo "записан $DIR/SHA256SUMS — сохрани копию на X230"
fi
