#!/usr/bin/env bash
# Сборка FFmpeg для Kovapp на ПК: только то, что нужно для подготовки видео
# перед отправкой — чтение распространённых форматов (в том числе AV1 через dav1d),
# сжатие в H.264 (OpenH264) со звуком AAC в MP4 и снимок-обложка JPEG.
# Без частей под GPL: FFmpeg — LGPL 2.1, OpenH264 и dav1d — BSD. Всё статически, без DLL.
#
#   build_ffmpeg.sh win64 <папка исходников> <папка результата> <bin llvm-mingw>
#   build_ffmpeg.sh linux <папка исходников> <папка результата>
#
# В папке исходников: ffmpeg (тег n8.1.3), openh264 (тег v2.6.0), dav1d (тег 1.5.1) — без правок.
# Нужны nasm, ninja и meson (переменная MESON, например «python3 meson/meson.py»).
# Результат: <папка результата>/ffmpeg(.exe), ffprobe(.exe), LICENSE-*, versions.txt
set -euo pipefail

TARGET="${1:?win64 или linux}"
SRC="$(cd "${2:?папка исходников}" && pwd)"
OUT="$(mkdir -p "${3:?папка результата}" && cd "$3" && pwd)"
TC="${4:-}"
JOBS="$(nproc 2>/dev/null || echo 2)"
WORK="$OUT/.build"
PREFIX="$WORK/prefix"
mkdir -p "$WORK" "$PREFIX"

if [ "$TARGET" = "win64" ]; then
  [ -n "$TC" ] || { echo "нужна папка bin llvm-mingw"; exit 1; }
  export PATH="$TC:$PATH"
  HOST=x86_64-w64-mingw32
  # libc++ из llvm-mingw вместо libstdc++: OpenH264 написан на C++
  OH264=(OS=mingw_nt ARCH=x86_64 CC="$HOST-clang" CXX="$HOST-clang++" AR="$HOST-ar" STATIC_LDFLAGS="-lc++")
  FF_CROSS=(--enable-cross-compile --target-os=mingw32 --arch=x86_64 --cross-prefix="$HOST-" --cc="$HOST-clang" --cxx="$HOST-clang++")
  THREADS=--enable-w32threads
  EXE=.exe
else
  OH264=(STATIC_LDFLAGS="-lstdc++ -lpthread -lm")
  FF_CROSS=()
  THREADS=--enable-pthreads
  EXE=
fi

# ── OpenH264 (Cisco, BSD-2): кодировщик H.264, только статическая библиотека ─
rm -rf "$WORK/openh264" && cp -a "$SRC/openh264" "$WORK/openh264"
cd "$WORK/openh264"
make -j"$JOBS" "${OH264[@]}" PREFIX="$PREFIX" libopenh264.a
make "${OH264[@]}" PREFIX="$PREFIX" install-static

# ── dav1d: декодер AV1 — только чтение исходника, сжимаем всегда в H.264 ─────
read -r -a MESON_CMD <<< "${MESON:-meson}"
DAV1D_CROSS=()
if [ "$TARGET" = "win64" ]; then
  cat > "$WORK/cross-win64.txt" <<CROSS
[binaries]
c = '$HOST-clang'
cpp = '$HOST-clang++'
ar = '$HOST-ar'
strip = '$HOST-strip'
windres = '$HOST-windres'
[host_machine]
system = 'windows'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'
CROSS
  DAV1D_CROSS=(--cross-file "$WORK/cross-win64.txt")
fi
rm -rf "$WORK/dav1d-build"
"${MESON_CMD[@]}" setup "$WORK/dav1d-build" "$SRC/dav1d" "${DAV1D_CROSS[@]}" --prefix="$PREFIX" --libdir=lib \
  --buildtype=release --default-library=static -Denable_tools=false -Denable_tests=false -Denable_examples=false
ninja -C "$WORK/dav1d-build" -j"$JOBS"
ninja -C "$WORK/dav1d-build" install

# ── FFmpeg: только нужные части ─────────────────────────────────────────────
DEMUXERS="mov,matroska,avi,flv,mpegts,mpegps,asf,ogg,h264,hevc,m4v,mjpeg,image2"
DECODERS="h264,hevc,libdav1d,mpeg4,msmpeg4v1,msmpeg4v2,msmpeg4v3,h263,h263p,mpeg1video,mpeg2video,mjpeg,vp8,vp9,theora,wmv1,wmv2,wmv3,vc1,flv,prores,dnxhd,dvvideo,rawvideo,png,qtrle"
DECODERS="$DECODERS,aac,aac_latm,mp3,mp3float,mp2,mp2float,ac3,eac3,opus,vorbis,flac,alac,amrnb,amrwb,wmav1,wmav2,wmapro,dca,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s24be,pcm_s32le,pcm_f32le,pcm_u8,pcm_alaw,pcm_mulaw,adpcm_ima_qt,adpcm_ms"
ENCODERS="libopenh264,aac,mjpeg"
MUXERS="mp4,mov,image2,image2pipe,mjpeg,null"
PARSERS="h264,hevc,av1,mpeg4video,mpegvideo,vp8,vp9,aac,aac_latm,ac3,mpegaudio,opus,vorbis,flac,h263,vc1,mjpeg,dca"
BSFS="aac_adtstoasc,h264_mp4toannexb,hevc_mp4toannexb,extract_extradata,null"
FILTERS="scale,fps,format,crop,transpose,hflip,vflip,rotate,setsar,null,copy,trim,setpts,aresample,aformat,anull,atrim,asetpts"
PROTOCOLS="file,pipe"

rm -rf "$WORK/ffmpeg" && cp -a "$SRC/ffmpeg" "$WORK/ffmpeg"
cd "$WORK/ffmpeg"
PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig" ./configure "${FF_CROSS[@]}" \
  --prefix="$PREFIX" --pkg-config=pkg-config --pkg-config-flags=--static \
  --extra-cflags="-I$PREFIX/include" --extra-ldflags="-L$PREFIX/lib -static" \
  --enable-static --disable-shared "$THREADS" \
  --disable-debug --disable-doc --disable-htmlpages --disable-manpages --disable-podpages --disable-txtpages \
  --disable-autodetect --disable-network --disable-ffplay --disable-avdevice \
  --enable-ffmpeg --enable-ffprobe --enable-swscale --enable-swresample --enable-avfilter \
  --disable-everything \
  --enable-libopenh264 --enable-libdav1d \
  --enable-demuxer="$DEMUXERS" --enable-decoder="$DECODERS" --enable-encoder="$ENCODERS" \
  --enable-muxer="$MUXERS" --enable-parser="$PARSERS" --enable-bsf="$BSFS" \
  --enable-filter="$FILTERS" --enable-protocol="$PROTOCOLS"
grep -q "^CONFIG_GPL=yes" ffbuild/config.mak && { echo "в сборку попала часть под GPL"; exit 1; }
make -j"$JOBS"
cp "ffmpeg$EXE" "ffprobe$EXE" "$OUT/"
if [ "$TARGET" = "win64" ]; then
  x86_64-w64-mingw32-strip "$OUT/ffmpeg$EXE" "$OUT/ffprobe$EXE"
else
  strip "$OUT/ffmpeg$EXE" "$OUT/ffprobe$EXE"
fi

# ── лицензии и версии: по LGPL вместе с программой — тексты и ссылки на исходники ─
{
  echo "FFmpeg $(cat "$SRC/ffmpeg/RELEASE" 2>/dev/null) ($(git -C "$SRC/ffmpeg" rev-parse HEAD 2>/dev/null || echo '?')), LGPL 2.1 или новее"
  echo "  https://github.com/FFmpeg/FFmpeg/tree/n$(cat "$SRC/ffmpeg/RELEASE" 2>/dev/null)"
  echo "OpenH264 $(git -C "$SRC/openh264" describe --tags 2>/dev/null || echo '?') ($(git -C "$SRC/openh264" rev-parse HEAD 2>/dev/null || echo '?')), BSD 2-Clause"
  echo "  https://github.com/cisco/openh264"
  echo "dav1d $(git -C "$SRC/dav1d" describe --tags 2>/dev/null || echo '?') ($(git -C "$SRC/dav1d" rev-parse HEAD 2>/dev/null || echo '?')), BSD 2-Clause"
  echo "  https://code.videolan.org/videolan/dav1d  (зеркало: https://github.com/videolan/dav1d)"
  echo "Цель: $TARGET; собрано: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "Состав: сжатие libopenh264 + aac, обложка mjpeg; чтение AV1 — libdav1d; разбор: $DEMUXERS"
  echo "Сборка: tools/ffmpeg/build_ffmpeg.sh (ПК-версия Kovapp)"
} > "$OUT/versions.txt"
rm -f "$OUT"/LICENSE-*.txt
cp "$SRC/ffmpeg/COPYING.LGPLv2.1" "$OUT/LICENSE-FFmpeg-LGPLv2.1.txt"
cp "$SRC/openh264/LICENSE" "$OUT/LICENSE-OpenH264-BSD2.txt"
cp "$SRC/dav1d/COPYING" "$OUT/LICENSE-dav1d-BSD2.txt"
rm -rf "$WORK"
echo "Готово: $OUT"
