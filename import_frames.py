#!/usr/bin/env python3
"""Import 1–8 individual walking poses; run --help for supported options."""

import argparse
from pathlib import Path
import re
import sys
import tempfile

from frame_geometry import CANVAS, CONTENT_H, GROUND

try:
    from PIL import Image, ImageChops, ImageDraw, ImageOps
except ImportError:
    raise SystemExit('缺少 Pillow，请运行: python -m pip install -r requirements-import.txt')

ROOT = Path(__file__).resolve().parent
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
PREVIEW = 'walk_preview.png'


def natural_key(path):
    return [int(part) if part.isdigit() else part.casefold()
            for part in re.split(r'(\d+)', str(path))]


def collect_inputs(items):
    paths = []
    for item in items:
        path = Path(item).expanduser()
        if path.is_dir():
            paths.extend(sorted((p for p in path.iterdir()
                                 if p.is_file() and p.suffix.lower() in EXTENSIONS
                                 and p.name != PREVIEW), key=natural_key))
        elif path.is_file() and path.suffix.lower() in EXTENSIONS:
            paths.append(path)
        else:
            raise ValueError(f'输入不存在或格式不支持: {path}')
    paths = list(dict.fromkeys(p.resolve() for p in paths))
    if not 1 <= len(paths) <= 8:
        raise ValueError(f'需要 1–8 张单帧图片，实际找到 {len(paths)} 张；不自动切割拼图')
    return paths


def cut_background(image, white_threshold=240, shadow_threshold=210):
    """Remove only edge-connected white/light neutral gray, keeping enclosed fur.

    Shadows are a heuristic: only neutral gray in the lower half is eligible.
    Existing alpha is retained; transparent regions also seed the flood fill.
    """
    r, g, b, alpha = image.split()
    low = ImageChops.darker(ImageChops.darker(r, g), b)
    high = ImageChops.lighter(ImageChops.lighter(r, g), b)
    white = low.point(lambda value: 255 if value >= white_threshold else 0)
    if shadow_threshold:
        bright = low.point(lambda value: 255 if value >= shadow_threshold else 0)
        neutral = ImageChops.subtract(high, low).point(lambda value: 255 if value <= 12 else 0)
        shadow = ImageChops.multiply(bright, neutral)
        shadow.paste(0, (0, 0, image.width, image.height // 2))
        white = ImageChops.lighter(white, shadow)
    transparent = alpha.point(lambda value: 255 if value == 0 else 0)
    eligible = ImageChops.lighter(white, transparent)
    # One connected outer border seeds all four edges, including corner cutouts.
    padded = ImageOps.expand(eligible, border=1, fill=255)
    ImageDraw.floodfill(padded, (0, 0), 128)
    removed = padded.crop((1, 1, image.width + 1, image.height + 1)).point(
        lambda value: 255 if value == 128 else 0)
    result = image.copy()
    result.putalpha(ImageChops.subtract(alpha, removed))
    return result


def prepare(path, args):
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original).convert('RGBA')
    if not args.no_cut:
        image = cut_background(image, args.white_threshold, args.shadow_threshold)
    bbox = image.getchannel('A').getbbox()
    if bbox is None:
        raise ValueError(f'处理后没有可见内容: {path}')
    return image.crop(bbox)


def normalize(crops, fit):
    # Keep aspect ratios and never clip a wide tail. Uniform uses one scale;
    # per-frame maximizes each pose independently within the same bounds.
    ratios = [min(CONTENT_H / im.height, (CANVAS - 2) / im.width) for im in crops]
    common = min(ratios)
    frames = []
    for crop, ratio in zip(crops, ratios):
        ratio = common if fit == 'uniform' else ratio
        size = (max(1, round(crop.width * ratio)), max(1, round(crop.height * ratio)))
        resized = crop.resize(size, Image.Resampling.LANCZOS)
        # Resampling may leave an entirely transparent outer row/column.
        resized = resized.crop(resized.getchannel('A').getbbox())
        frame = Image.new('RGBA', (CANVAS, CANVAS))
        frame.alpha_composite(resized, ((CANVAS - resized.width) // 2, GROUND - resized.height))
        frames.append(frame)
    return frames


def make_preview(frames):
    preview = Image.new('RGB', (CANVAS * len(frames), CANVAS), '#26303a')
    for index, frame in enumerate(frames):
        preview.paste(frame, (index * CANVAS, 0), frame.getchannel('A'))
    draw = ImageDraw.Draw(preview)
    draw.line((0, GROUND, preview.width, GROUND), fill='#73d6b1', width=1)
    for index in range(len(frames)):
        draw.text((index * CANVAS + 10, 10), str(index + 1), fill='white')
    return preview


def byte_value(value):
    number = int(value)
    if not 0 <= number <= 255:
        raise argparse.ArgumentTypeError('必须是 0–255 的整数')
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', nargs='*', help='图片或文件夹；省略时使用仓库 source/cat_walk_*_src.jpg')
    parser.add_argument('--out', type=Path, default=ROOT / 'image', help='输出目录（默认脚本旁 image/）')
    parser.add_argument('--fit', choices=('uniform', 'per-frame'), default='uniform', help='统一比例或逐帧适配')
    parser.add_argument('--no-cut', action='store_true', help='保留输入 alpha，跳过白底和阴影处理')
    parser.add_argument('--white-threshold', type=byte_value, default=240, help='白底通道下限（默认 240）')
    parser.add_argument('--shadow-threshold', type=byte_value, default=210, help='下半部浅灰阴影下限（默认 210，0 禁用）')
    parser.add_argument('--overwrite', action='store_true', help='替换输出并删除多余的旧编号帧；禁止覆盖输入')
    args = parser.parse_args(argv)
    try:
        items = args.inputs or sorted((ROOT / 'source').glob('cat_walk_*_src.jpg'), key=natural_key)
        inputs = collect_inputs(items)
        out = args.out.expanduser().resolve()
        targets = [out / f'cat_walk_{i}.png' for i in range(1, len(inputs) + 1)]
        old = [out / f'cat_walk_{i}.png' for i in range(1, 9)]
        affected = set(old + [out / PREVIEW])
        if set(inputs) & affected:
            raise ValueError('输出会覆盖输入，请用 --out 指定另一个目录')
        existing = [p for p in affected if p.exists()]
        if existing and not args.overwrite:
            raise ValueError('输出目录已有走路帧或预览；请先用 --out 指定新目录，确认后再用 --overwrite')
        # Decode and validate every input before touching existing output.
        frames = normalize([prepare(path, args) for path in inputs], args.fit)
        preview = make_preview(frames)
        out.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.import-', dir=out) as staging:
            staging = Path(staging)
            for target, frame in zip(targets, frames):
                frame.save(staging / target.name)
            preview.save(staging / PREVIEW)
            for target in targets + [out / PREVIEW]:
                (staging / target.name).replace(target)
            for stale in old[len(frames):]:
                if stale.exists():
                    stale.unlink()
        for source, target in zip(inputs, targets):
            print(f'{source.name} -> {target}')
        print(f'预览: {out / PREVIEW}')
        return 0
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    sys.exit(main())
