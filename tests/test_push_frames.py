# -*- coding: utf-8 -*-
"""推真实图标的专用推帧测试: 4 张 cat_push_1..4.png。

分两层, 按 test_paw.py / test_push_icons.py 的规矩如实报告:
- 纯逻辑测试不依赖 PyQt5: 帧文件本身(PNG 头 + 解码后确有透明像素)、
  config.py 常量、pet.py / frames.py 接线结构。即使本机没装 PyQt5,
  这些也能被测到。
- 离屏 GUI 测试需要 PyQt5(QT_QPA_PLATFORM=offscreen), 缺失时自动跳过,
  不冒充通过: 用内存假后端(不碰真 Explorer)走真实模式——
  走到图标旁播推帧、播完推图标; 推帧缺了回落扒拉帧照常推;
  推帧扒拉帧都没了不崩。
"""

import ast
import json
import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from PyQt5.QtCore import QEvent, QPointF, QSettings, Qt
    from PyQt5.QtGui import QMouseEvent
    from PyQt5.QtWidgets import QApplication
    from ragdollpet.config import Store
    from ragdollpet.desktop_icons import IconLayoutBackup as GuiBackup
    from ragdollpet.pet import DesktopPet
    PYQT5_AVAILABLE = True
except ImportError:
    PYQT5_AVAILABLE = False


def _load_module_ast(name):
    return ast.parse((ROOT / "ragdollpet" / name).read_text(encoding="utf-8"))


def _png_info(path):
    """只用标准库读 PNG: 返回 (宽, 高, 位深, 颜色类型, 最小 alpha, 最大 alpha)。

    颜色类型 6 = RGBA; 解开 IDAT 后逐行反滤波, 确认真的有透明像素。
    """
    data = Path(path).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"不是 PNG: {path}"
    pos = 8
    width = height = bitdepth = colortype = None
    idat = b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bitdepth, colortype = struct.unpack(">IIBB", chunk[:10])
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + length
    assert width is not None, f"没有 IHDR: {path}"
    raw = zlib.decompress(idat)
    assert colortype == 6 and bitdepth == 8, f"不是 8 位 RGBA: {path}"
    channels = 4
    stride = width * channels
    out = bytearray()
    prev = bytearray(stride)
    i = 0
    for _ in range(height):
        f = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        if f == 1:
            for x in range(channels, stride):
                line[x] = (line[x] + line[x - channels]) & 0xFF
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif f == 3:
            for x in range(stride):
                a = line[x - channels] if x >= channels else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 0xFF
        elif f == 4:
            for x in range(stride):
                a = line[x - channels] if x >= channels else 0
                b = prev[x]
                c = prev[x - channels] if x >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xFF
        out += line
        prev = line
    alphas = out[3::4]
    return width, height, bitdepth, colortype, min(alphas), max(alphas)


class PushFrameFilesTests(unittest.TestCase):
    """推帧文件本身: 4 张 600x600、8 位 RGBA、真的有透明像素。"""

    def test_push_frames_are_600x600_rgba_transparent(self):
        for i in range(1, 5):
            path = ROOT / "image" / f"cat_push_{i}.png"
            self.assertTrue(path.exists(), f"缺图: {path}")
            w, h, depth, ctype, amin, amax = _png_info(path)
            self.assertEqual((w, h), (600, 600), path.name)
            self.assertEqual((depth, ctype), (8, 6), path.name)
            self.assertEqual(amin, 0, f"{path.name} 没有全透明像素")
            self.assertEqual(amax, 255, f"{path.name} 没有不透明像素")


class PushFramesConstantsTests(unittest.TestCase):
    """config.py: IMG_PUSH 是 4 个推帧文件名; 每帧 450ms。"""

    def test_img_push_constant(self):
        tree = _load_module_ast("config.py")
        found = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in ("IMG_PUSH", "ACTION_MS"):
                    found[node.targets[0].id] = ast.literal_eval(node.value)
        self.assertEqual(found.get("IMG_PUSH"),
                         [f"cat_push_{i}.png" for i in range(1, 5)])
        self.assertEqual(found.get("ACTION_MS"), 450)


class PushWiringStructureTests(unittest.TestCase):
    """pet.py / frames.py 接线: 推帧进动作帧池, 真实模式播推帧, 缺图回落。"""

    def test_start_action_pool_contains_push(self):
        src = (ROOT / "ragdollpet" / "pet.py").read_text(encoding="utf-8")
        self.assertIn('"push": self.pix_push', src)
        self.assertIn("self.pix_push = [pm for pm in (self._load_image(n) for n in IMG_PUSH)",
                      src)
        self.assertIn("self.pix_push_l = [p for p in (self._flipped(pm) for pm in self.pix_push) if p]",
                      src)
        self.assertIn("IMG_PUSH", src)
        # 走到图标后: 有推帧播推帧, 没推帧回落扒拉帧, on_done 照常推图标
        self.assertIn('kind = "push" if self.pix_push else "paw"', src)
        self.assertIn("on_done=self._push_icon_after_paw", src)

    def test_frame_priority_push_branch(self):
        src = (ROOT / "ragdollpet" / "frames.py").read_text(encoding="utf-8")
        self.assertIn('if self._action_kind == "push" and self.pix_push:', src)
        self.assertIn('return (f"push{\'_l\' if self.facing < 0 else \'\'}_{idx}", pool[idx])',
                      src)
        # 推帧分支在扒拉分支之后、被摸之前
        self.assertLess(src.index('"push"'), src.index("self._petting and self.pix_pet"))
        self.assertLess(src.index('"paw"'), src.index('"push"'))


class FakeIconBackend:
    """内存假后端: 行为像真后端, 不碰 Explorer。"""

    def __init__(self, icons=None):
        self.enabled = True
        self._icons = {ic["name"]: [ic["x"], ic["y"]] for ic in (icons or [])}
        self.set_calls = []

    def list_icons(self):
        return [{"name": n, "x": p[0], "y": p[1], "index": i}
                for i, (n, p) in enumerate(self._icons.items())]

    def get_position(self, name):
        for n, p in self._icons.items():
            if n.lower() == name.lower():
                return tuple(p)
        return None

    def set_position(self, name, x, y):
        self.set_calls.append((name, int(x), int(y)))
        for n in self._icons:
            if n.lower() == name.lower():
                self._icons[n] = [int(x), int(y)]
                return True
        return False

    def desktop_dir(self):
        return None

    def real_file_icons(self):
        return self.list_icons()


OLD_PET_JSON = {
    "scale_val": 0.5, "ontop": True, "water_on": True, "water_min": 45,
    "sleep_min": 5, "deep_sleep_min": 10, "anim_on": True, "power_saver": False,
    "high_dpi": True, "anni_name": "", "anni_date": "", "last_anni_shown": "",
    "pos_x": 1161, "pos_y": 597,
}


@unittest.skipUnless(PYQT5_AVAILABLE, "需要 PyQt5")
class PushFramesGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "t.json"), "w", encoding="utf-8") as f:
            json.dump(OLD_PET_JSON, f, ensure_ascii=False)
        qs = QSettings(os.path.join(self.tmp, "t.ini"), QSettings.IniFormat)
        self.store = Store(qs)
        self.pet = DesktopPet(self.store)
        self.icon_x = self.pet.x() - 200
        self.fake = FakeIconBackend(
            [{"name": "报告.docx", "x": self.icon_x, "y": 100}])
        self.pet._icons = self.fake
        self.pet._icon_backup = GuiBackup(
            os.path.join(self.tmp, "icon_layout_backup.json"))

    def tearDown(self):
        try:
            self.pet.shutdown()
        except Exception:
            pass
        self.app.processEvents()

    def _drive_action_to_end(self):
        for _ in range(10):
            if not self.pet._doing_action():
                break
            self.pet._on_action_tick()
        self.assertFalse(self.pet._doing_action())

    def _drive_walk_to_arrival(self, limit=2000):
        for _ in range(limit):
            if self.pet._doing_action():
                return True
            if self.pet.state != "walk":
                return False
            self.pet._on_walk_tick()
        return self.pet._doing_action()

    def test_push_frames_load(self):
        self.assertEqual(len(self.pet.pix_push), 4)
        self.assertEqual(len(self.pet.pix_push_l), 4)
        for pm in self.pet.pix_push:
            self.assertFalse(pm.isNull())

    def test_real_mode_plays_push_frames_not_paw(self):
        """真实模式: 走到图标旁播推帧(4 帧一轮约 1.8 秒), 播完照常推图标。"""
        self.assertTrue(self.pet._start_paw())
        self.assertTrue(self._drive_walk_to_arrival())
        self.assertEqual(self.pet._action_kind, "push")
        self.assertEqual(self.pet._action_left, 4)
        self.assertTrue(self.pet._frame()[0].startswith("push"))
        self.pet._on_action_tick()
        self.assertTrue(self.pet._frame()[0].startswith("push_1"))
        # 翻转: 朝左时帧名带 _l, 用翻转过的遮罩
        self.pet.facing = -1
        self.assertTrue(self.pet._frame()[0].startswith("push_l"))
        self.pet.facing = 1
        self._drive_action_to_end()
        new_pos = self.fake.get_position("报告.docx")
        self.assertIsNotNone(new_pos)
        self.assertNotEqual(new_pos, (self.icon_x, 100))
        self.assertEqual(self.pet._icon_backup.get("报告.docx"),
                         (self.icon_x, 100))

    def test_missing_push_frames_falls_back_to_paw_and_still_pushes(self):
        """推帧缺了: 回落到扒拉帧播一次, 播完照常推图标。"""
        self.pet.pix_push = []
        self.pet.pix_push_l = []
        self.assertTrue(self.pet._start_paw())
        self.assertTrue(self._drive_walk_to_arrival())
        self.assertEqual(self.pet._action_kind, "paw")
        self.assertTrue(self.pet._frame()[0].startswith("paw"))
        self._drive_action_to_end()
        new_pos = self.fake.get_position("报告.docx")
        self.assertIsNotNone(new_pos)
        self.assertNotEqual(new_pos, (self.icon_x, 100))
        self.assertEqual(self.pet._icon_backup.get("报告.docx"),
                         (self.icon_x, 100))

    def test_missing_push_and_paw_frames_does_not_crash(self):
        """推帧扒拉帧都没了: 走到图标旁后不播也不推, 不崩, 图标不动。"""
        self.pet.pix_push = []
        self.pet.pix_push_l = []
        self.pet.pix_paw = []
        self.pet.pix_paw_l = []
        self.assertTrue(self.pet._start_paw())
        self.assertFalse(self._drive_walk_to_arrival())
        self.assertFalse(self.pet._doing_action())
        self.assertIsNone(self.pet._push_target)
        self.assertEqual(self.pet.state, "idle")
        self.assertEqual(self.fake.get_position("报告.docx"),
                         (self.icon_x, 100))


if __name__ == "__main__":
    unittest.main()
