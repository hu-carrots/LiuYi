# -*- coding: utf-8 -*-
"""推真实桌面图标测试: 冒险版(走过去推真的图标)。

分两层, 按 test_paw.py 的规矩如实报告:
- 纯逻辑测试不依赖 PyQt5: desktop_icons.py 是标准库 only,
  直接加载: 夹取、显示名比对、推的方向和距离、走向目标点、
  备份记录与恢复、非 Windows 下静默关闭、Win32 常量与结构、
  config.py 台词模板、pet.py 接线结构。即使本机没装 PyQt5,
  这些也能被测到。
- 离屏 GUI 测试需要 PyQt5(QT_QPA_PLATFORM=offscreen), 缺失时自动跳过,
  不冒充通过: 用内存假后端(不碰真 Explorer)走完整流程——
  走到图标旁、扒拉、推一把、备份、恢复、回落、取消。
"""

import ast
import json
import math
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_standalone(name):
    """不经过 ragdollpet/__init__.py(它会 import PyQt5)直接加载标准库模块。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        f"push_icons_standalone_{name}", ROOT / "ragdollpet" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_icons = _load_standalone("desktop_icons")
clamp_icon_pos = _icons.clamp_icon_pos
display_matches_file = _icons.display_matches_file
push_step = _icons.push_step
walk_step_toward = _icons.walk_step_toward
target_pet_x = _icons.target_pet_x
choose_icon = _icons.choose_icon
restore_positions = _icons.restore_positions
IconLayoutBackup = _icons.IconLayoutBackup
DesktopIconBackend = _icons.DesktopIconBackend

try:
    from PyQt5.QtCore import QEvent, QPointF, QSettings, Qt
    from PyQt5.QtGui import QMouseEvent
    from PyQt5.QtWidgets import QApplication
    from ragdollpet.config import Store
    from ragdollpet.desktop_icons import IconLayoutBackup as GuiBackup
    from ragdollpet.pet import DesktopPet
    import ragdollpet.pet as pet_mod
    PYQT5_AVAILABLE = True
except ImportError:
    PYQT5_AVAILABLE = False


class _ZeroJitterRng:
    """假随机: 方向不偏, 挑第一个; 让推的方向和距离可断言。"""

    def uniform(self, a, b):
        return 0.0

    def choice(self, seq):
        return seq[0]


class FakeIconBackend:
    """内存假后端: 行为像真后端, 不碰 Explorer。

    enabled=True 让菜单入口可用; fail_names 里名字 set_position 失败。
    """

    def __init__(self, icons=None):
        self.enabled = True
        self._icons = {ic["name"]: [ic["x"], ic["y"]] for ic in (icons or [])}
        self.fail_names = set()
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
        if name in self.fail_names:
            return False
        for n in self._icons:
            if n.lower() == name.lower():
                self._icons[n] = [int(x), int(y)]
                return True
        return False

    def desktop_dir(self):
        return None

    def real_file_icons(self):
        return self.list_icons()


# ================================ 纯逻辑: 夹取 ================================
class ClampIconPosTests(unittest.TestCase):
    RECT = (0, 0, 1920, 1040)     # left, top, right, bottom

    def test_inside_unchanged(self):
        self.assertEqual(clamp_icon_pos(100, 100, self.RECT), (100, 100))

    def test_left_top_edges(self):
        self.assertEqual(clamp_icon_pos(0, 0, self.RECT), (0, 0))
        self.assertEqual(clamp_icon_pos(-50, -20, self.RECT), (0, 0))

    def test_right_bottom_edges_leave_one_icon(self):
        # 右/下边留一个图标身位(80px): x 最大 1920-80, y 最大 1040-80
        self.assertEqual(clamp_icon_pos(1900, 1000, self.RECT), (1840, 960))
        self.assertEqual(clamp_icon_pos(9999, 9999, self.RECT), (1840, 960))

    def test_bad_input_does_not_crash(self):
        self.assertEqual(clamp_icon_pos("x", None, self.RECT), (0, 0))
        self.assertEqual(clamp_icon_pos(100, 100, "nope"), (0, 0))


# ================================ 纯逻辑: 显示名比对 ================================
class DisplayMatchesFileTests(unittest.TestCase):
    def test_exact(self):
        self.assertTrue(display_matches_file("报告.docx", "报告.docx"))

    def test_case_insensitive(self):
        self.assertTrue(display_matches_file("Report.DOCX", "report.docx"))

    def test_shortcut_strips_lnk(self):
        self.assertTrue(display_matches_file("Chrome", "Chrome.lnk"))
        self.assertTrue(display_matches_file("chrome", "Chrome.lnk"))

    def test_hidden_extension(self):
        # 资源管理器藏扩展名时显示名没后缀, 也算同一个文件
        self.assertTrue(display_matches_file("报告", "报告.docx"))

    def test_system_icons_skipped(self):
        self.assertFalse(display_matches_file("此电脑", "报告.docx"))
        self.assertFalse(display_matches_file("回收站", "Chrome.lnk"))

    def test_no_match(self):
        self.assertFalse(display_matches_file("报告.docx", "总结.docx"))
        self.assertFalse(display_matches_file("", "报告.docx"))
        self.assertFalse(display_matches_file("报告.docx", ""))
        self.assertFalse(display_matches_file(None, "报告.docx"))
        self.assertFalse(display_matches_file("报告.docx", None))


# ================================ 纯逻辑: 推的方向和距离 ================================
class PushStepTests(unittest.TestCase):
    def test_push_right_32px(self):
        self.assertEqual(push_step(100, 100, 0.0, rng=_ZeroJitterRng()),
                         (132, 100))

    def test_push_left_32px(self):
        self.assertEqual(push_step(100, 100, 180.0, rng=_ZeroJitterRng()),
                         (68, 100))

    def test_push_down_32px(self):
        self.assertEqual(push_step(100, 100, 90.0, rng=_ZeroJitterRng()),
                         (100, 132))

    def test_jitter_changes_direction(self):
        class _TiltRng:
            def uniform(self, a, b):
                return 30.0
        # 0° 偏 +30°: x = 100 + 32*cos30 ≈ 128, y = 100 + 32*sin30 = 116
        self.assertEqual(push_step(100, 100, 0.0, rng=_TiltRng()), (128, 116))

    def test_distance_is_32(self):
        x, y = push_step(100, 100, 45.0, rng=_ZeroJitterRng())
        self.assertAlmostEqual(math.hypot(x - 100, y - 100), 32, delta=1.0)

    def test_bad_input_does_not_crash(self):
        self.assertEqual(push_step("x", None, 0.0), (0, 0))


# ================================ 纯逻辑: 走向目标点 ================================
class WalkStepTowardTests(unittest.TestCase):
    def test_step_right(self):
        self.assertEqual(walk_step_toward(100, 500, 2), (102, False))

    def test_step_left(self):
        self.assertEqual(walk_step_toward(500, 100, 2), (498, False))

    def test_arrived_within_40(self):
        # 离目标 40px 内就算到了, 不用走到正下方
        self.assertEqual(walk_step_toward(460, 500, 2), (460, True))
        self.assertEqual(walk_step_toward(540, 500, 2), (540, True))

    def test_overshoot_stops_on_target(self):
        # 一步跨过目标: 停在目标上, 不冲过去(450 离 500 有 50px, 不算已到)
        self.assertEqual(walk_step_toward(450, 500, 100), (500, True))

    def test_bad_input_stops(self):
        self.assertEqual(walk_step_toward("x", 500, 2), (0, True))


class TargetPetXTests(unittest.TestCase):
    def test_centers_under_icon(self):
        # 图标左上角 500(格子约 80 宽, 中心 540), 猫宽 110 -> 目标 x = 485
        self.assertEqual(target_pet_x(500, 110, 0, 1920), 485)

    def test_clamped_to_area(self):
        self.assertEqual(target_pet_x(-100, 110, 0, 1920), 0)
        self.assertEqual(target_pet_x(1900, 110, 0, 1920), 1810)


class ChooseIconTests(unittest.TestCase):
    def test_none_when_empty(self):
        self.assertIsNone(choose_icon([]))
        self.assertIsNone(choose_icon(None))

    def test_picks_one(self):
        icons = [{"name": "a"}, {"name": "b"}]
        self.assertEqual(choose_icon(icons, rng=_ZeroJitterRng()), {"name": "a"})


# ================================ 纯逻辑: 备份 ================================
class IconBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "icon_layout_backup.json")

    def test_record_only_first_time(self):
        bak = IconLayoutBackup(self.path)
        self.assertTrue(bak.record("报告.docx", (100, 200)))
        self.assertFalse(bak.record("报告.docx", (300, 400)))
        self.assertEqual(bak.get("报告.docx"), (100, 200))

    def test_save_load_round_trip(self):
        bak = IconLayoutBackup(self.path)
        bak.record("报告.docx", (100, 200))
        bak.record("Chrome", (300, 400))
        bak2 = IconLayoutBackup(self.path)
        self.assertEqual(bak2.get("报告.docx"), (100, 200))
        self.assertEqual(bak2.get("Chrome"), (300, 400))
        self.assertEqual(len(bak2), 2)

    def test_retain_only_failed_restores_across_restart(self):
        bak = IconLayoutBackup(self.path)
        bak.record("ok", (100, 200))
        bak.record("failed", (300, 400))
        self.assertTrue(bak.retain(["failed"]))
        self.assertEqual(bak.items(), [("failed", (300, 400))])
        self.assertEqual(IconLayoutBackup(self.path).items(),
                         [("failed", (300, 400))])

    def test_retain_write_failure_preserves_original(self):
        bak = IconLayoutBackup(self.path)
        bak.record("ok", (100, 200))
        bak.record("failed", (300, 400))
        with mock.patch.object(os, "replace", side_effect=OSError("disk full")):
            self.assertFalse(bak.retain(["failed"]))
        self.assertEqual(len(bak), 2)
        self.assertEqual(len(IconLayoutBackup(self.path)), 2)

    def test_retain_empty_removes_backup(self):
        bak = IconLayoutBackup(self.path)
        bak.record("ok", (100, 200))
        self.assertTrue(bak.retain([]))
        self.assertFalse(bak)
        self.assertFalse(os.path.exists(self.path))

    def test_clear_removes_memory_and_file(self):
        bak = IconLayoutBackup(self.path)
        bak.record("报告.docx", (100, 200))
        self.assertTrue(os.path.exists(self.path))
        bak.clear()
        self.assertFalse(bak)
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(IconLayoutBackup(self.path).items(), [])

    def test_corrupt_file_is_empty_backup(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{不是 json")
        bak = IconLayoutBackup(self.path)
        self.assertFalse(bak)
        self.assertEqual(bak.items(), [])

    def test_bad_record_inputs(self):
        bak = IconLayoutBackup(self.path)
        self.assertFalse(bak.record("", (1, 2)))
        self.assertFalse(bak.record(None, (1, 2)))
        self.assertFalse(bak.record("a", ("x", None)))
        self.assertFalse(bak)


class RestorePositionsTests(unittest.TestCase):
    RECT = (0, 0, 1920, 1040)

    def test_restores_all(self):
        backend = FakeIconBackend([{"name": "报告.docx", "x": 500, "y": 500},
                                   {"name": "Chrome", "x": 600, "y": 600}])
        bak = IconLayoutBackup(os.path.join(tempfile.mkdtemp(), "b.json"))
        bak.record("报告.docx", (100, 200))
        bak.record("Chrome", (300, 400))
        ok, failed = restore_positions(backend, bak, self.RECT)
        self.assertEqual((ok, failed), (2, []))
        self.assertEqual(backend.get_position("报告.docx"), (100, 200))
        self.assertEqual(backend.get_position("Chrome"), (300, 400))

    def test_one_failure_does_not_stop_others(self):
        backend = FakeIconBackend([{"name": "报告.docx", "x": 500, "y": 500},
                                   {"name": "Chrome", "x": 600, "y": 600}])
        backend.fail_names = {"Chrome"}
        bak = IconLayoutBackup(os.path.join(tempfile.mkdtemp(), "b.json"))
        bak.record("报告.docx", (100, 200))
        bak.record("Chrome", (300, 400))
        ok, failed = restore_positions(backend, bak, self.RECT)
        self.assertEqual(ok, 1)
        self.assertEqual(failed, ["Chrome"])
        self.assertEqual(backend.get_position("报告.docx"), (100, 200))
        self.assertEqual(backend.get_position("Chrome"), (600, 600))

    def test_restore_clamps_to_area(self):
        backend = FakeIconBackend([{"name": "报告.docx", "x": 500, "y": 500}])
        bak = IconLayoutBackup(os.path.join(tempfile.mkdtemp(), "b.json"))
        bak.record("报告.docx", (9999, 9999))
        ok, failed = restore_positions(backend, bak, self.RECT)
        self.assertEqual((ok, failed), (1, []))
        self.assertEqual(backend.get_position("报告.docx"), (1840, 960))


# ================================ 纯逻辑: 非 Windows 静默关闭 ================================
class NonWindowsBackendTests(unittest.TestCase):
    def test_backend_is_disabled_here(self):
        # 这台是 Linux: 后端必须静默关闭, 不碰 ctypes.windll
        backend = DesktopIconBackend()
        self.assertFalse(backend.enabled)
        self.assertFalse(_icons.IS_WINDOWS)
        self.assertEqual(backend.list_icons(), [])
        self.assertIsNone(backend.get_position("报告.docx"))
        self.assertFalse(backend.set_position("报告.docx", 1, 2))
        self.assertEqual(backend.real_file_icons(), [])
        self.assertIsNone(backend.desktop_dir())


# ================================ 结构: 常量与结构 ================================
class Win32ConstantsTests(unittest.TestCase):
    def test_lvm_constants(self):
        # 事先核对过的值(见 desktop_icons.py 顶部): LVM_FIRST = 0x1000
        self.assertEqual(_icons.LVM_GETITEMCOUNT, 0x1004)
        self.assertEqual(_icons.LVM_SETITEMPOSITION, 0x100F)
        self.assertEqual(_icons.LVM_GETITEMPOSITION, 0x1010)
        self.assertEqual(_icons.LVM_GETITEMTEXTW, 0x1073)
        self.assertEqual(_icons.LVIF_TEXT, 0x0001)

    def test_lvitem_field_order(self):
        # LVITEMW 字段顺序必须和 Windows SDK 一致, 否则远程读写错位
        names = [f[0] for f in _icons._LVITEMW._fields_]
        self.assertEqual(names, ["mask", "iItem", "iSubItem", "state", "stateMask",
                                 "pszText", "cchTextMax", "iImage", "lParam",
                                 "iIndent", "iGroupId", "cColumns", "puColumns",
                                 "piColFmt", "iGroup"])
        # pszText 必须在 mask/iItem/iSubItem/state/stateMask 之后(偏移 24)
        self.assertEqual(_icons._LVITEMW.pszText.offset, 24)

    def test_play_constants(self):
        self.assertEqual(_icons.PUSH_DISTANCE_PX, 32)
        self.assertEqual(_icons.ICON_MARGIN_PX, 80)
        self.assertEqual(_icons.WALK_ARRIVE_PX, 40)
        self.assertEqual(_icons.ICON_BACKUP_FILENAME, "icon_layout_backup.json")


def _load_module_ast(name):
    return ast.parse((ROOT / "ragdollpet" / name).read_text(encoding="utf-8"))


def _assigned_lists(tree, names):
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in names \
                and isinstance(node.value, ast.List):
            out[node.targets[0].id] = [
                e.value for e in node.value.elts if isinstance(e, ast.Constant)]
    return out


class PushConfigTests(unittest.TestCase):
    """config.py 台词模板: AST 读源码, 不 import PyQt5。"""

    def test_push_lines_have_name_placeholder(self):
        tree = _load_module_ast("config.py")
        got = _assigned_lists(tree, {"LINES_PUSH"})
        self.assertGreaterEqual(len(got["LINES_PUSH"]), 3)
        for line in got["LINES_PUSH"]:
            self.assertIn("{name}", line)
            self.assertTrue(line.strip())
        all_lines = " ".join(got["LINES_PUSH"])
        self.assertNotIn("鲸", all_lines)
        self.assertNotIn("大肥鱼", all_lines)

    def test_no_new_pet_json_keys(self):
        """推图标不新增 pet.json 键。"""
        tree = _load_module_ast("config.py")
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) \
                    and node.targets[0].id == "DEFAULTS" \
                    and isinstance(node.value, ast.Dict):
                keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
        self.assertFalse(any("icon" in k or "push" in k for k in keys))
        example = json.loads((ROOT / "pet.example.json").read_text(encoding="utf-8"))
        self.assertEqual(set(example), set(keys))


class PushCodeStructureTests(unittest.TestCase):
    """pet.py / desktop_icons.py 的接线方式: 源码结构断言, 不 import Qt。"""

    def test_backend_methods_present(self):
        src = (ROOT / "ragdollpet" / "desktop_icons.py").read_text(encoding="utf-8")
        for name in ("def list_icons", "def get_position", "def set_position",
                     "def real_file_icons", "def desktop_dir",
                     "def _find_listview", "def _remote_alloc",
                     "def _remote_write", "def _remote_read",
                     "class DesktopIconBackend", "class IconLayoutBackup"):
            self.assertIn(name, src)
        # 非 Windows 守卫: import 时不碰 windll, 方法返回安全空值
        self.assertIn('IS_WINDOWS = sys.platform == "win32"', src)
        self.assertIn("if not self.enabled:", src)

    def test_pet_wiring_present(self):
        src = (ROOT / "ragdollpet" / "pet.py").read_text(encoding="utf-8")
        for frag in ("def _start_walk_to_icon", "def _cancel_icon_walk",
                     "def _on_target_walk_tick", "def _try_start_real_paw",
                     "def _on_icon_arrived", "def _push_icon_after_paw",
                     "def _restore_icons", "def _manual_push_icon",
                     '"推一下桌面图标"', '"把图标放回去"',
                     "def _update_menu_state"):
            self.assertIn(frag, src)
        # 扒拉先试真实模式, 不行回落道具纸
        self.assertIn("if self._try_start_real_paw():", src)
        # 动作播完回调: 推图标只在播完后发生, 取消不推
        self.assertIn("on_done", src)
        self.assertIn("self._action_on_done = None", src)

    def test_backup_file_ignored(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("icon_layout_backup.json", gitignore)


OLD_PET_JSON = {
    "scale_val": 0.5, "ontop": True, "water_on": True, "water_min": 45,
    "sleep_min": 5, "deep_sleep_min": 10, "anim_on": True, "power_saver": False,
    "high_dpi": True, "anni_name": "", "anni_date": "", "last_anni_shown": "",
    "pos_x": 1161, "pos_y": 597,
}


@unittest.skipUnless(PYQT5_AVAILABLE, "需要 PyQt5")
class PushIconsGuiTests(unittest.TestCase):
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
        # 假后端 + 临时目录备份, 不碰真 Explorer、不往仓库里写文件
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
        """手动推走路定时器, 直到新动作开始(推帧 kind="push" 或扒拉 kind="paw")。"""
        for _ in range(limit):
            if self.pet._doing_action():
                return True
            if self.pet.state != "walk":
                return False
            self.pet._on_walk_tick()
        return self.pet._doing_action()

    def test_real_mode_walk_push_frames(self):
        """完整流程: 走过去 → 推帧(4 帧) → 图标挪约 32px → 备份记了原始位置。"""
        self.assertTrue(self.pet._start_paw())
        self.assertEqual(self.pet.state, "walk")
        self.assertIsNotNone(self.pet._walk_target_x)
        self.assertTrue(self._drive_walk_to_arrival())
        self.assertEqual(self.pet.state, "idle")
        self.assertEqual(self.pet._action_kind, "push")
        self.assertEqual(self.pet._action_left, 4)
        self.assertTrue(self.pet._frame()[0].startswith("push"))
        self._drive_action_to_end()
        new_pos = self.fake.get_position("报告.docx")
        self.assertIsNotNone(new_pos)
        # 朝左推约 32px(方向有随机偏, x 分量 27~32, y 分量 ±16)
        self.assertTrue(25 <= self.icon_x - new_pos[0] <= 33,
                        (self.icon_x, new_pos))
        self.assertTrue(abs(new_pos[1] - 100) <= 17, new_pos)
        self.assertEqual(self.pet._icon_backup.get("报告.docx"),
                         (self.icon_x, 100))
        self.assertTrue(os.path.exists(
            os.path.join(self.tmp, "icon_layout_backup.json")))

    def test_no_icons_falls_back_to_prop(self):
        """没图标: 回落到原地扒拉道具纸, 图标不动、也不走路。"""
        self.pet._icons = FakeIconBackend([])
        self.assertTrue(self.pet._start_paw())
        self.assertEqual(self.pet._action_kind, "paw")
        self.assertIsNone(self.pet._walk_target_x)
        self.assertEqual(self.pet.state, "idle")
        self._drive_action_to_end()

    def test_stay_mode_falls_back_to_prop(self):
        """不是 free 模式: 不走过去, 原地扒拉道具纸。"""
        self.pet.mode = "stay"
        self.assertTrue(self.pet._start_paw())
        self.assertEqual(self.pet._action_kind, "paw")
        self.assertIsNone(self.pet._walk_target_x)
        self.assertEqual(self.fake.get_position("报告.docx"),
                         (self.icon_x, 100))
        self._drive_action_to_end()

    def test_push_failure_degrades_gracefully(self):
        """推不动: 不崩, 回待机, 图标没动; 备份已记原始位置(推之前记的)。"""
        self.fake.fail_names = {"报告.docx"}
        self.assertTrue(self.pet._start_paw())
        self.assertTrue(self._drive_walk_to_arrival())
        self._drive_action_to_end()
        self.assertEqual(self.pet.state, "idle")
        self.assertFalse(self.pet._doing_action())
        self.assertEqual(self.fake.get_position("报告.docx"),
                         (self.icon_x, 100))
        self.assertEqual(self.pet._icon_backup.get("报告.docx"),
                         (self.icon_x, 100))

    def test_grab_cancels_target_walk(self):
        """走向图标半路被抓住: 取消走路, 不推, 回待机。"""
        self.assertTrue(self.pet._start_paw())
        self.assertEqual(self.pet.state, "walk")
        event = QMouseEvent(QEvent.MouseButtonPress, QPointF(10, 10),
                            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        self.pet.mousePressEvent(event)
        self.assertIsNone(self.pet._walk_target_x)
        self.assertEqual(self.pet.state, "idle")
        self.assertIsNone(self.pet._action_kind)
        self.assertEqual(self.fake.get_position("报告.docx"),
                         (self.icon_x, 100))

    def test_restore_icons(self):
        """推完再恢复: 图标回原始位置, 备份清空(内存 + 文件)。"""
        self.assertTrue(self.pet._start_paw())
        self.assertTrue(self._drive_walk_to_arrival())
        self._drive_action_to_end()
        moved = self.fake.get_position("报告.docx")
        self.assertNotEqual(moved, (self.icon_x, 100))
        self.pet._restore_icons()
        self.assertEqual(self.fake.get_position("报告.docx"),
                         (self.icon_x, 100))
        self.assertFalse(self.pet._icon_backup)
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp, "icon_layout_backup.json")))

    def test_menu_entries_and_enable_states(self):
        menu = self.pet._build_menu()
        texts = [a.text() for a in menu.actions()]
        self.assertIn("推一下桌面图标", texts)
        self.assertIn("把图标放回去", texts)
        # 没备份: 推可用(假后端 enabled), 恢复变灰
        self.pet._update_menu_state()
        self.assertTrue(self.pet._act_push_icon.isEnabled())
        self.assertFalse(self.pet._act_restore_icons.isEnabled())
        # 记一条备份: 恢复可用
        self.pet._icon_backup.record("报告.docx", (self.icon_x, 100))
        self.pet._update_menu_state()
        self.assertTrue(self.pet._act_restore_icons.isEnabled())
        # 做动作时两项都变灰
        self.assertTrue(self.pet._start_paw())
        self.pet._update_menu_state()
        self.assertFalse(self.pet._act_push_icon.isEnabled())
        self.assertFalse(self.pet._act_restore_icons.isEnabled())

    def test_manual_push_icon(self):
        self.pet._manual_push_icon()
        self.assertEqual(self.pet.state, "walk")
        self.assertTrue(self._drive_walk_to_arrival())
        self._drive_action_to_end()
        self.assertNotEqual(self.fake.get_position("报告.docx"),
                            (self.icon_x, 100))


if __name__ == "__main__":
    unittest.main()
