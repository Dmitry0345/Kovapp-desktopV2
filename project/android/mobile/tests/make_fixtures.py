#!/usr/bin/env python3
"""Готовит образцы для сверки Java-кода приложения с веб-версией.

Нужны ffmpeg (с libx264, libx265, libvpx, libopus, libmp3lame), Pillow и Node.
Результат — папка tests/fixtures с видео, картинками, двоичными файлами и
reference.json: что про те же файлы говорит оригинальный JS веб-версии.

    python tests/make_fixtures.py
"""
import io
import json
import os
import random
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'fixtures')


def ff(*args):
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y'] + list(args)
    subprocess.run(cmd, check=True, cwd=OUT)


def lavfi_video(size, rate, dur, extra_src=''):
    return ['-f', 'lavfi', '-i', 'testsrc2=size=%s:rate=%s:duration=%s%s' % (size, rate, dur, extra_src)]


def lavfi_audio(dur, freq=440):
    return ['-f', 'lavfi', '-i', 'sine=frequency=%d:duration=%s:sample_rate=48000' % (freq, dur)]


def rotate(src, dst, degrees):
    ff('-display_rotation:v:0', str(degrees), '-i', src, '-c', 'copy', dst)
    os.remove(os.path.join(OUT, src))


def videos():
    made = []
    # H.264 High + AAC, B-кадры (ctts, elst), поворот
    ff(*lavfi_video('640x360', 30, 3), *lavfi_audio(3), '-c:v', 'libx264', '-profile:v', 'high', '-bf', '2',
       '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '96k', 'tmp_rot.mp4')
    rotate('tmp_rot.mp4', 'h264_aac_rot.mp4', 90)
    made.append('h264_aac_rot.mp4')
    # «уже готово»: 1280x720 25 к/с, низкий битрейт, moov в начале
    ff(*lavfi_video('1280x720', 25, 2), *lavfi_audio(2), '-c:v', 'libx264', '-profile:v', 'main', '-crf', '36',
       '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', 'h264_ready_faststart.mp4')
    made.append('h264_ready_faststart.mp4')
    # высокий битрейт и 60 к/с — сжимать нужно
    ff(*lavfi_video('1920x1080', 60, 1), '-c:v', 'libx264', '-profile:v', 'high', '-crf', '10', '-pix_fmt', 'yuv420p',
       'h264_60fps_noaudio.mp4')
    made.append('h264_60fps_noaudio.mp4')
    # HEVC + Opus
    ff(*lavfi_video('640x480', 30, 2), *lavfi_audio(2), '-c:v', 'libx265', '-x265-params', 'log-level=error',
       '-tag:v', 'hvc1', '-pix_fmt', 'yuv420p', '-c:a', 'libopus', '-b:a', '64k', 'hevc_opus.mp4')
    made.append('hevc_opus.mp4')
    # MOV с MP3 (esds, oti 0x6B) и поворотом на 270
    ff(*lavfi_video('480x270', 24, 2), *lavfi_audio(2), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
       '-c:a', 'libmp3lame', '-b:a', '96k', 'tmp_mp3.mov')
    rotate('tmp_mp3.mov', 'h264_mp3_rot.mov', -90)
    made.append('h264_mp3_rot.mov')
    # VP9 в MP4 (vpcC)
    ff(*lavfi_video('320x240', 30, 1), '-c:v', 'libvpx-vp9', '-b:v', '300k', '-deadline', 'realtime', '-cpu-used', '8',
       'vp9.mp4')
    made.append('vp9.mp4')
    # AV1 (av1C)
    ff(*lavfi_video('320x240', 30, 1), '-c:v', 'libsvtav1', '-preset', '12', '-pix_fmt', 'yuv420p', 'av1.mp4')
    made.append('av1.mp4')
    # фрагментированный MP4 — веб-версия отказывается, Java тоже должна
    ff(*lavfi_video('320x240', 30, 2), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', 'frag_keyframe+empty_moov',
       'fragmented.mp4')
    made.append('fragmented.mp4')
    # две звуковые дорожки
    ff(*lavfi_video('320x240', 30, 1), *lavfi_audio(1, 440), *lavfi_audio(1, 880), '-map', '0:v', '-map', '1:a', '-map', '2:a',
       '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', 'two_audio.mp4')
    made.append('two_audio.mp4')
    # MJPEG в MOV — неизвестное семейство
    ff(*lavfi_video('320x240', 15, 1), '-c:v', 'mjpeg', '-q:v', '5', 'mjpeg.mov')
    made.append('mjpeg.mov')
    # 3GP (та же ISO BMFF)
    ff(*lavfi_video('176x144', 15, 1), '-c:v', 'libx264', '-profile:v', 'baseline', '-pix_fmt', 'yuv420p', '-f', '3gp', 'small.3gp')
    made.append('small.3gp')
    # мусор
    with open(os.path.join(OUT, 'garbage.mp4'), 'wb') as f:
        f.write(bytes(random.Random(5).getrandbits(8) for _ in range(5000)))
    made.append('garbage.mp4')
    return made


def images():
    from PIL import Image
    made = []

    def grad(w, h, mode='RGB'):
        img = Image.new(mode, (w, h))
        px = img.load()
        for y in range(h):
            for x in range(w):
                v = (x * 255 // max(1, w - 1), y * 255 // max(1, h - 1), (x + y) % 256)
                px[x, y] = v + ((x * 7 + y) % 256,) if mode == 'RGBA' else v
        return img

    base = grad(400, 300)
    exif = Image.Exif()
    exif[0x0112] = 6
    base.save(os.path.join(OUT, 'jpeg_exif6.jpg'), 'JPEG', quality=90, exif=exif.tobytes())
    made.append('jpeg_exif6.jpg')
    grad(1200, 800).save(os.path.join(OUT, 'jpeg_progressive.jpg'), 'JPEG', quality=85, progressive=True)
    made.append('jpeg_progressive.jpg')
    grad(300, 200, 'RGBA').save(os.path.join(OUT, 'png_rgba.png'))
    made.append('png_rgba.png')
    grad(300, 200).save(os.path.join(OUT, 'png_rgb.png'))
    made.append('png_rgb.png')
    pal = grad(120, 80).convert('P')
    pal.info['transparency'] = 0
    pal.save(os.path.join(OUT, 'png_pal.png'), transparency=0)
    made.append('png_pal.png')
    grad(160, 90).save(os.path.join(OUT, 'anim.gif'))
    made.append('anim.gif')
    grad(640, 480).save(os.path.join(OUT, 'webp_lossy.webp'), 'WEBP', quality=80)
    made.append('webp_lossy.webp')
    grad(333, 222).save(os.path.join(OUT, 'webp_lossless.webp'), 'WEBP', lossless=True)
    made.append('webp_lossless.webp')
    grad(500, 250, 'RGBA').save(os.path.join(OUT, 'webp_alpha.webp'), 'WEBP', quality=80)
    made.append('webp_alpha.webp')
    grad(77, 55).save(os.path.join(OUT, 'bmp.bmp'))
    made.append('bmp.bmp')
    return made


def blobs():
    made = []
    rnd = random.Random(42)
    for size in (0, 1, 1000, 4 * 1024 * 1024 - 1, 4 * 1024 * 1024, 4 * 1024 * 1024 + 1, 9 * 1024 * 1024 + 123):
        name = 'blob_%d.bin' % size
        with open(os.path.join(OUT, name), 'wb') as f:
            f.write(rnd.randbytes(size))
        made.append(name)
    return made


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    lists = {'videos.json': videos(), 'images.json': images(), 'blobs.json': blobs()}
    for name, items in lists.items():
        with open(os.path.join(OUT, name), 'w', encoding='utf-8') as f:
            json.dump(items, f)
    subprocess.run(['node', os.path.join(HERE, 'node', 'dump_reference.js'), OUT], check=True)
    subprocess.run([sys.executable, os.path.join(HERE, 'python', 'push_vectors.py'), os.path.join(OUT, 'push_vectors.json')], check=True)
    open(os.path.join(OUT, 'READY'), 'w').close()
    print('образцы готовы:', OUT)


if __name__ == '__main__':
    if '--blobs' in sys.argv:
        # только большие случайные образцы (без ffmpeg): их нет в архиве, они детерминированы
        os.makedirs(OUT, exist_ok=True)
        blobs()
    else:
        main()
