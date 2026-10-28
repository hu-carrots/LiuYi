from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

import import_frames as importer
from frame_geometry import CANVAS, CONTENT_H, GROUND

ROOT = Path(__file__).resolve().parents[1]


class ImportTests(unittest.TestCase):
    def test_edge_cut_preserves_enclosed_white_and_alpha(self):
        image = Image.new('RGBA', (80, 80), 'white')
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 10, 59, 59), fill='brown')
        draw.rectangle((25, 15, 54, 54), fill='white')
        draw.rectangle((5, 65, 74, 70), fill=(220, 220, 220))
        image.putpixel((30, 30), (255, 255, 255, 90))
        result = importer.cut_background(image)
        self.assertEqual(result.getpixel((0, 0))[3], 0)
        self.assertEqual(result.getpixel((10, 67))[3], 0)
        self.assertEqual(result.getpixel((40, 40))[3], 255)
        self.assertEqual(result.getpixel((30, 30))[3], 90)
        self.assertEqual(importer.cut_background(image, shadow_threshold=0).getpixel((10, 67))[3], 255)

    def test_fit_baseline_center_and_wide_pose(self):
        crops = [Image.new('RGBA', size, 'brown') for size in ((100, 200), (100, 100), (1000, 100))]
        for mode in ('uniform', 'per-frame'):
            frames = importer.normalize(crops, mode)
            boxes = [frame.getchannel('A').getbbox() for frame in frames]
            for frame, box in zip(frames, boxes):
                self.assertEqual(frame.size, (CANVAS, CANVAS))
                self.assertEqual(box[3], GROUND)
                self.assertLessEqual(abs((box[0] + box[2]) / 2 - CANVAS / 2), .5)
                self.assertGreaterEqual(box[0], 1)
            if mode == 'uniform':
                self.assertEqual(boxes[0][2] - boxes[0][0], boxes[1][2] - boxes[1][0])
            else:
                self.assertEqual(boxes[0][3] - boxes[0][1], CONTENT_H)
                self.assertEqual(boxes[1][3] - boxes[1][1], CONTENT_H)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'import_frames.py'), *map(str, args)],
                              cwd=ROOT.parent, capture_output=True, text=True)

    def test_repository_pngs_cli_and_overwrite(self):
        sources = [ROOT / 'image' / f'cat_walk_{i}.png' for i in range(1, 5)]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'out'
            result = self.run_cli(*sources, '--no-cut', '--fit', 'per-frame', '--out', out)
            self.assertEqual(result.returncode, 0, result.stderr)
            for i in range(1, 5):
                with Image.open(out / f'cat_walk_{i}.png') as frame:
                    self.assertEqual(frame.mode, 'RGBA')
                    self.assertEqual(frame.size, (600, 600))
                    self.assertEqual(frame.getchannel('A').getbbox()[3], GROUND)
            self.assertTrue((out / 'walk_preview.png').exists())
            before = (out / 'cat_walk_1.png').read_bytes()
            result = self.run_cli(*sources[:1], '--no-cut', '--out', out)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((out / 'cat_walk_1.png').read_bytes(), before)
            result = self.run_cli(*sources[:1], '--no-cut', '--out', out, '--overwrite')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((out / 'cat_walk_4.png').exists())
            self.assertFalse((out / 'cat_walk_2.png').exists())
            result = self.run_cli(out / 'cat_walk_1.png', '--out', out, '--overwrite')
            self.assertNotEqual(result.returncode, 0)

    def test_default_repository_jpegs(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli('--out', tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(list(Path(tmp).glob('cat_walk_*.png'))), 4)
            for path in Path(tmp).glob('cat_walk_*.png'):
                with Image.open(path) as frame:
                    self.assertEqual(frame.size, (CANVAS, CANVAS))
                    self.assertEqual(frame.mode, 'RGBA')
                    self.assertEqual(frame.getpixel((0, 0))[3], 0)
                    self.assertEqual(frame.getchannel('A').getbbox()[3], GROUND)
                    # Enclosed central white fur must remain visible.
                    self.assertEqual(frame.getpixel((300, 300))[3], 255)

    def test_natural_order_and_invalid_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('pose10.png', 'pose2.png', 'pose1.png', 'walk_preview.png'):
                Image.new('RGBA', (20, 20), 'brown').save(root / name)
            self.assertEqual([p.name for p in importer.collect_inputs([root])],
                             ['pose1.png', 'pose2.png', 'pose10.png'])
            self.assertEqual(len(importer.collect_inputs([root / 'pose2.png', root / 'pose2.png'])), 1)
            result = self.run_cli(root / 'missing.png', '--out', root / 'out')
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / 'out').exists())
            result = self.run_cli(root / 'pose1.png', '--white-threshold', '256')
            self.assertNotEqual(result.returncode, 0)
            for i in range(9):
                Image.new('RGBA', (20, 20), 'brown').save(root / f'extra{i}.png')
            with self.assertRaises(ValueError):
                importer.collect_inputs([root])

    def test_empty_frame_does_not_modify_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'blank.png'
            Image.new('RGBA', (20, 20)).save(source)
            out = root / 'out'
            out.mkdir()
            target = out / 'cat_walk_1.png'
            target.write_bytes(b'existing')
            result = self.run_cli(source, '--out', out, '--overwrite', '--no-cut')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(target.read_bytes(), b'existing')


if __name__ == '__main__':
    unittest.main()
