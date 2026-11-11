# -*- coding: utf-8 -*-
"""
================================================================================
 桌面宠物 · Q版布偶猫  v2.2
--------------------------------------------------------------------------------
 这是 ragdollpet 包里的「主窗口」模块。完整程序由六个模块组成:
   config.py   常量 / 台词 / 路径 / 配置(pet.json) / 系统空闲检测
   assets.py   素材查找( image/ → 脚本目录 → assets/ → 工作目录 )
   frames.py   帧选择 / 缩放缓存 / 窗口遮罩 / 载入时自动归一化
   bubble.py   对话气泡
   pet.py      本文件: 主窗口(状态机、绘制、菜单、设置)
   app.py      单实例锁 + 入口

 依赖:   pip install PyQt5==5.15.11
 运行:   双击 desktop_pet_v2.1.py(启动器), 或 python desktop_pet_v2.1.py
 打包:   pyinstaller --noconsole --onefile --name RagdollPet ^
           --add-data "image;image" desktop_pet_v2.1.py
--------------------------------------------------------------------------------
 素材(都放 image/ 下, 统一 600x600 / 内容高 482 / 脚底 549 / 中心 300):
   cat_idle.png       睁眼端坐   → 待机(必需)
   cat_blink.png      闭眼       → 眨眼瞬间(可选, 缺了就用整体压扁)
   cat_sleep.png      趴卧睡觉   → 浅睡 / 深睡
   cat_click.png      炸毛       → 单击跳跃 / 连戳炸毛
   cat_pet.png        眯眼被摸   → 单击(可选, 缺了退回炸毛跳)
   cat_walk_1..8.png  步态循环   → 走动(可选, 缺了退回 idle)
   cat_walk_a/b/c.png 旧命名, 仅在没有编号帧时兜底
   cat_idle_breath_1/2.png 待机呼吸(吸气/呼气, 可选, 缺了退回单张待机 + 程序化呼吸)
   cat_sleep_breath_1/2.png 睡觉呼吸(吸气/呼气, 可选, 缺了退回单张睡觉 + 程序化呼吸)
 找不到图时用 QPainter 简笔画兜底, 不崩。
--------------------------------------------------------------------------------
 v2.2 改动:
   [重构] 单文件 1500 行拆成 6 个模块, 启动器只负责 sys.path + main()
   [配置] QSettings(注册表) → pet.json; 首次启动自动迁移, 之后原子写入
   [新增] 闭眼帧 cat_blink.png: 有就真闭眼, 没有退回压扁式眨眼
   [新增] 载入时自动归一化: 非 600x600 的透明图自动对齐到标准基线
   [改进] 走路起伏改正弦(原先是"抬 2px / 落 0"两档, 4 帧时显得僵)
   [修复] app_dir() 在包里取到了包目录 → 资源与配置路径整体错位
   [修复] 设置保存时两条气泡连发, DPI 提示被"设置好啦"盖掉
   [修复] 退出时没停抖动定时器; 载入归一化不再逐像素扫描
--------------------------------------------------------------------------------
 历史关键修复(均在真机实测过):
   [性能] mask 由 Python 逐像素改 createAlphaMask()(C++): 7.3ms→0.14ms, 2x 28ms→0.74ms
   [修复] Zzz 被 mask 裁掉 / 跳跃顶点被窗口顶边削掉 / 转身后用到反的遮罩
   [修复] 走动被打断后永不恢复 / 关动画再开不散步 / 非单调时钟计时错
   [修复] 气泡贴窗口顶(离头顶 95px) → 按当前帧内容顶端算, 稳定 6px
   [体验] 单实例; 位置记忆; 拖拽边界钳制; 多屏适配; 高 DPI 按 dpr 出图
================================================================================
"""


import math
import os
import random
import sys
import threading
import time
import traceback
from datetime import date, datetime

from PyQt5.QtCore import (
    QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF,
    QSequentialAnimationGroup, QSize, QTimer, Qt, pyqtProperty,
)
from PyQt5.QtGui import (
    QBrush, QColor, QCursor, QFont, QPainter, QPen, QPixmap, QRegion,
)
from PyQt5.QtWidgets import (
    QAction, QActionGroup, QApplication, QMenu, QWidget,
)

from .assets import resource_path
from .bubble import SpeechBubble
from .config import (
    ACTION_MS, BASE_H, BLINK_RATIO, BUBBLE_GAP, DOUBLE_CLICK_SEC, FRAME_MS,
    IMG_BLINK, IMG_CLICK, IMG_IDLE, IMG_IDLE_BREATH, IMG_PET, IMG_SLEEP,
    IMG_SLEEP_BREATH, IMG_STRETCH, IMG_WALK_LEGACY,
    IMG_WALK_SEQ, IMG_YAWN, INSTANCE_TITLE,
    BREATH_MS, EAT_MS, IMG_EAT, IMG_HOLD, JUMP_HEIGHT, LINES_ANGRY, LINES_BACK, LINES_CLICK,
    LINES_COMFORT, LINES_DRAG, LINES_FEED, LINES_IDLE, LINES_LAUNCH, LINES_PRAISE,
    LINES_SLEEP, LINES_STRETCH, LINES_TEASE, LINES_YAWN, LOGIC_MS, MASK_INFLATE,
    PET_MS, PET_NAME, SCALE_MAX, SCALE_MIN, TOP_MARGIN, Store, app_dir,
    get_global_idle_seconds,
)
from .frames import FrameMixin
from .phase1 import (
    LAYERS, MODES, SIZE_PRESETS, WAKE_STRETCH_CHANCE, AppUsageTracker, DailyStats,
    DndQueue, category_enabled, clamp_opacity, default_reminders, due_reminders,
    format_weather_line, next_breath_index, next_idle_interval, parse_reminder,
    pick_idle_action, should_advance_breath, should_trigger_idle_action,
    snap_position,
)
from . import platform_win
from .weather import WeatherError, fetch_weather


# ================================ 桌面宠物 ================================
class DesktopPet(FrameMixin, QWidget):

    def _get_offset_y(self) -> int:
        return self._dy

    def _set_offset_y(self, value):
        value = int(value)
        if value != self._dy:
            self._dy = value
            self.update()

    offset_y = pyqtProperty(int, fget=_get_offset_y, fset=_set_offset_y)

    def __init__(self, store: Store):
        super().__init__()
        self.store = store

        self.setWindowTitle(INSTANCE_TITLE)   # 第二个实例靠标题找到这只猫
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        # ---------- 配置 ----------
        self.scale = max(SCALE_MIN, min(store.get_float("scale_val"), SCALE_MAX))
        self.always_on_top = store.get_bool("ontop")
        self.water_enabled = store.get_bool("water_on")
        self.water_interval = store.get_int("water_min")
        self.sleep_minutes = store.get_int("sleep_min")
        self.deep_sleep_min = store.get_int("deep_sleep_min")
        self.anim_enabled = store.get_bool("anim_on")
        self.power_saver = store.get_bool("power_saver")
        self.anni_name = store.get_str("anni_name")
        self.anni_date = store.get_str("anni_date")

        # ---------- 配置(第一期) ----------
        self.speech_tier = store.get_str("speech_tier") or "normal"
        self.speech_categories = store.get_dict("speech_categories")
        self.mode = store.get_str("mode") or "free"
        if self.mode not in MODES:
            self.mode = "free"
        self.window_layer = store.get_str("window_layer") or "top"
        if self.window_layer not in LAYERS:
            self.window_layer = "top"
        # 迁移: 旧 pet.json 没有 window_layer 时, 按老 ontop 推导一次并落盘,
        # 之后以 window_layer 为准(置顶开关和层级菜单改的是同一个值)。
        try:
            import json as _json
            with open(store.path, encoding="utf-8") as _f:
                _raw = _json.load(_f)
            if isinstance(_raw, dict) and "window_layer" not in _raw:
                self.window_layer = "top" if self.always_on_top else "normal"
                store.set("window_layer", self.window_layer)
        except (OSError, ValueError):
            pass
        self.opacity = clamp_opacity(store.get_float("opacity"))
        self.locked = store.get_bool("locked")
        self.click_through = store.get_bool("click_through")
        self.autostart = store.get_bool("autostart")
        self.size_preset = store.get_str("size_preset")
        self.reminders = []
        for raw in store.get_list("reminders") or default_reminders():
            try:
                self.reminders.append(parse_reminder(raw))
            except ValueError:
                continue
        if not self.reminders:
            self.reminders = default_reminders()
        self.dnd_default_min = max(1, store.get_int("dnd_default_min"))
        self.weather_on = store.get_bool("weather_on")
        self.weather_city = store.get_str("weather_city")
        self.app_watch = store.get_list("app_watch")
        self.app_launch_on = store.get_bool("app_launch_on")
        self.hotkeys = store.get_list("hotkeys")
        # 今日统计: 跨天自动清零(存在 pet.json 的扁平键里, 向后兼容)
        today = date.today().isoformat()
        if store.get_str("stats_date") != today:
            self._stats = DailyStats(date_str=today)
            self._persist_stats()
        else:
            self._stats = DailyStats.from_store(today, store.data)
        self._dnd = DndQueue()
        self._app_tracker = AppUsageTracker(self.app_watch)
        self._weather_result = None     # 后台线程查天气的结果 {"done": bool, ...}
        self._hotkeys_registered = False
        self._hotkey_map = {}           # RegisterHotKey id -> 快捷键记录
        self._follow_target = None

        # ---------- 状态 ----------
        self.state = "idle"            # idle / walk / sleep / deep_sleep
        self.facing = 1
        self.last_interact = time.monotonic()

        # ---------- 动画量 ----------
        self._dy = 0
        self.blink = False
        self._blink_left = random.randint(60, 180)
        self.tail_dx = 0
        self._tail_left = random.randint(180, 400)
        self.zzz_phase = 0.0
        self._zzz_divider = 0
        self._jumping = False
        self._walk_left = 0
        self._walk_total = 0
        self._walk_bob = 0                # 走路时的身体起伏(画图时叠加)
        self._angry_until = 0.0           # 炸毛表情保持到这一时刻
        self._shake_left = 0              # 抖动剩余步数
        self._walk_screen = None          # 走动期间缓存的屏幕, 每帧重读可用区域

        # ---------- 缓存 ----------
        self._pix_cache = {}           # (帧名, w, h, dpr) -> QPixmap
        self._mask_cache = {}
        self._mask_key = None          # 当前 mask 是按哪一帧建的          # (帧名, w, h, 有Z) -> QRegion
        self._font_cache = {}
        self._top_cache = {}            # 帧名 -> 内容顶端占图片高度的比例(气泡贴头用)
        self._mask_broken = False
        self._err_time = 0.0

        # ---------- 触点 / 杂项 ----------
        self._drag_off = None
        self._press_pos = None
        self._dragging = False
        self._water_pending = False
        self._used = {}

        # ---------- 呼吸动画 ----------
        self.breath_up = QPropertyAnimation(self, b"offset_y", self)
        self.breath_up.setStartValue(0)
        self.breath_up.setEndValue(-3)
        self.breath_up.setEasingCurve(QEasingCurve.OutSine)
        self.breath_down = QPropertyAnimation(self, b"offset_y", self)
        self.breath_down.setStartValue(-3)
        self.breath_down.setEndValue(0)
        self.breath_down.setEasingCurve(QEasingCurve.InSine)
        self.breath_group = QSequentialAnimationGroup(self)
        self.breath_group.addAnimation(self.breath_up)
        self.breath_group.addAnimation(self.breath_down)
        self.breath_group.setLoopCount(-1)

        # ---------- 跳跃动画 ----------
        self.jump_up = QPropertyAnimation(self, b"offset_y", self)
        self.jump_up.setDuration(280)
        self.jump_up.setEndValue(-JUMP_HEIGHT)
        self.jump_up.setEasingCurve(QEasingCurve.OutQuad)
        self.jump_down = QPropertyAnimation(self, b"offset_y", self)
        self.jump_down.setDuration(320)
        self.jump_down.setStartValue(-JUMP_HEIGHT)
        self.jump_down.setEndValue(0)
        self.jump_down.setEasingCurve(QEasingCurve.InQuad)
        self.jump_group = QSequentialAnimationGroup(self)
        self.jump_group.addAnimation(self.jump_up)
        self.jump_group.addAnimation(self.jump_down)
        self.jump_group.finished.connect(self._on_jump_finished)

        # ---------- 吃东西动画(张嘴/闭嘴) ----------
        self._eat_timer = QTimer(self, timeout=self._on_eat_tick)

        # ---------- 新动作动画(伸懒腰/打哈欠, 各两帧循环, 共用一个定时器) ----------
        self._action_timer = QTimer(self, timeout=self._on_action_tick)

        # ---------- 呼吸帧动画(待机/睡觉, 基础帧 + 吸气 + 呼气, 每帧约 1.2 秒) ----------
        self._breath_timer = QTimer(self, timeout=self._on_breath_tick)

        # ---------- 资源 ----------
        self.pix_idle = self._load_image(IMG_IDLE)
        self.pix_sleep = self._load_image(IMG_SLEEP)
        self.pix_click = self._load_image(IMG_CLICK)
        self.pix_idle_left = self._flipped(self.pix_idle)
        # 步态帧: 编号命名优先(cat_walk_1..8), 没有就退回 A/B/C; 1 张也能跑
        walk_names = ([n for n in IMG_WALK_SEQ if os.path.exists(resource_path(n))]
                      or IMG_WALK_LEGACY)
        self.pix_walk = [pm for pm in (self._load_image(n) for n in walk_names)
                         if pm is not None and not pm.isNull()]
        self.pix_walk_l = [p for p in (self._flipped(pm) for pm in self.pix_walk) if p]
        self._walk_toggle = 0                              # 步态帧计数
        # 每帧停留多少 tick: 目标一轮 ~1 秒, 帧数越多每帧越短
        self._walk_step_ticks = max(4, min(14, round(30 / max(1, len(self.pix_walk)))))
        # 被摸帧(可选): 没有就沿用"炸毛跳"
        self.pix_pet = self._load_image(IMG_PET)
        self.pix_pet_l = self._flipped(self.pix_pet)
        # 闭眼帧(可选): 有就用真闭眼, 没有就把 idle 压扁当眨眼
        self.pix_blink = self._load_image(IMG_BLINK)
        self.pix_blink_l = self._flipped(self.pix_blink)
        # 被拎起(可选): 拖动时用
        self.pix_hold = self._load_image(IMG_HOLD)
        self.pix_hold_l = self._flipped(self.pix_hold)
        self._holding = False
        # 吃零食(可选): 张嘴/闭嘴两帧循环
        self.pix_eat = [pm for pm in (self._load_image(n) for n in IMG_EAT) if pm is not None]
        self.pix_eat_l = [p for p in (self._flipped(pm) for pm in self.pix_eat) if p]
        self._eating = False
        self._eat_index = 0
        self._eat_left = 0
        # 新动作(可选): 伸懒腰/打哈欠各两帧循环; 图缺了就降级(不触发、不崩)
        self.pix_stretch = [pm for pm in (self._load_image(n) for n in IMG_STRETCH)
                            if pm is not None and not pm.isNull()]
        self.pix_stretch_l = [p for p in (self._flipped(pm) for pm in self.pix_stretch) if p]
        self.pix_yawn = [pm for pm in (self._load_image(n) for n in IMG_YAWN)
                         if pm is not None and not pm.isNull()]
        self.pix_yawn_l = [p for p in (self._flipped(pm) for pm in self.pix_yawn) if p]
        self._action_kind = None       # None / "stretch" / "yawn": 当前在做哪个新动作
        self._action_index = 0
        self._action_left = 0
        self._last_action_at = time.monotonic()   # 上次新动作开始时间(待机随机触发的节流用)
        # 呼吸帧(可选): 待机/睡觉各两帧(吸气/呼气); 图缺了池子里只剩基础帧, 不循环也不崩
        self.pix_idle_breath = [pm for pm in (self._load_image(n) for n in IMG_IDLE_BREATH)
                                if pm is not None and not pm.isNull()]
        self.pix_idle_breath_l = [p for p in (self._flipped(pm) for pm in self.pix_idle_breath) if p]
        self.pix_sleep_breath = [pm for pm in (self._load_image(n) for n in IMG_SLEEP_BREATH)
                                 if pm is not None and not pm.isNull()]
        self._breath_index = 0        # 呼吸循环下标: 0 基础帧, 1 吸气, 2 呼气
        self._pending_sleep = False    # 睡前打哈欠进行中: 哈欠完再真正进 sleep
        self._petting = False
        self._last_click_at = 0.0
        self._missing_assets = self.pix_idle is None

        self.bubble = SpeechBubble()
        self._menu = None              # 懒建, 复用
        self._log_assets()             # 缺素材时把搜索路径打出来, 省得猜

        # ---------- 尺寸 / 位置 / mask ----------
        self._relayout()
        self._place_initial()
        self._rebuild_mask()
        self._apply_on_top(silent=True)

        # ---------- 定时器 ----------
        self._frame_timer = QTimer(self, timeout=self._on_frame)
        self._walk_timer = QTimer(self, timeout=self._on_walk_tick)
        self._logic_timer = QTimer(self, timeout=self._on_logic)
        self._logic_timer.start(LOGIC_MS)
        self._breath_timer.start(BREATH_MS)      # 一直转, 推进与否由 _on_breath_tick 守卫

        self._idle_talk = QTimer(self, timeout=self._on_idle_talk)
        self._idle_talk.start(next_idle_interval(self.speech_tier) * 1000)

        self._next_walk = QTimer(self, timeout=self._start_walk)
        self._next_walk.setSingleShot(True)
        self._schedule_walk(60, 120)

        self._water_timer = QTimer(self, timeout=self._on_water_remind)
        self._apply_water_timer()

        self._pet_timer = QTimer(self, timeout=self._end_pet)
        self._pet_timer.setSingleShot(True)

        self._apply_runtime()
        self._apply_layer(silent=True)
        self._apply_opacity()
        self._apply_click_through()
        # 天气后台查询的轮询(500ms 看一次线程结果, 不卡 UI)
        self._weather_timer = QTimer(self, timeout=self._on_weather_poll)
        self._weather_timer.start(500)

        QTimer.singleShot(1500, self._startup_line)


    # ---------------------------------------------------------------- 位置
    @staticmethod
    def _screen_at(point: QPoint):
        return QApplication.screenAt(point) or QApplication.primaryScreen()

    def _screen(self):
        return self._screen_at(self.frameGeometry().center())

    def _clamp_pos(self, pos: QPoint) -> QPoint:
        """拖拽/恢复位置时保证至少 80px 留在屏幕内。"""
        area = self._screen_at(
            pos + QPoint(self.width() // 2, self.height() // 2)).availableGeometry()
        x = max(area.left() - self.width() + 80, min(pos.x(), area.right() - 80))
        y = max(area.top(), min(pos.y(), area.bottom() - 80))
        return QPoint(x, y)

    def _visible_somewhere(self, pos: QPoint) -> bool:
        rect = QRect(pos, self.size())
        for screen in QApplication.screens():
            inter = rect.intersected(screen.availableGeometry())
            if inter.width() >= 60 and inter.height() >= 60:
                return True
        return False

    def _place_initial(self):
        saved = QPoint(self.store.get_int("pos_x"), self.store.get_int("pos_y"))
        if abs(saved.x()) < 20000 and self._visible_somewhere(saved):
            self.move(self._clamp_pos(saved))
            return
        area = QApplication.primaryScreen().availableGeometry()
        self.move(area.right() - self.width() - 60, area.bottom() - self.height() - 60)

    def _save_pos(self):
        self.store.set("pos_x", self.x())
        self.store.set("pos_y", self.y())

    def _apply_on_top(self, silent: bool = False):
        flags = self.windowFlags()
        if self.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        pos = self.pos()
        self.setWindowFlags(flags)            # 会重建原生窗口
        self.move(pos)                        # 位置可能被重置, 补回去
        self.show()
        self._rebuild_mask()                  # 重建窗口后要把 mask 重新贴上去
        if not silent:
            self.update()

    # ------------------------------------------------------------- 台词
    def _pick(self, pool):
        if not pool:
            return None
        used = self._used.setdefault(id(pool), set())
        avail = [i for i in range(len(pool)) if i not in used]
        if not avail:
            used.clear()
            avail = list(range(len(pool)))
        index = random.choice(avail)
        used.add(index)
        return pool[index]

    def _say(self, text: str, category: str = None):
        """所有台词的唯一出口。

        category: 语录类别, 关了整类就不说(触发类台词优先于闲聊, 但类别
        开关仍管自己的类); 免打扰期间不直接说, 先攒进队列, 结束再汇总。
        """
        if not text:
            return
        if category is not None and not category_enabled(self.speech_categories, category):
            return
        if self._dnd.active():
            self._dnd.queue(text)
            return
        self.bubble.say(text)

    def _persist_stats(self):
        for key, value in self._stats.to_store_dict().items():
            self.store.set(key, value)

    def _bump_stat(self, field: str):
        self._stats.bump(field)
        self.store.set("stat_" + field, self._stats.counts[field])

    # ------------------------------------------------------------- 状态
    def _sleeping(self) -> bool:
        return self.state in ("sleep", "deep_sleep")

    def _set_state(self, state: str):
        if state == self.state:
            return
        self.state = state
        self._breath_index = 0            # 换状态从基础帧重新开始呼吸循环
        if self._sleeping():
            self.blink = False
            self.tail_dx = 0
            self.zzz_phase = 0.0
            self._stop_walk()
            self._stop_action()            # 睡着就别继续伸懒腰/打哈欠了, 睡觉帧接管
            self._pending_sleep = False    # 深睡抢在睡前哈欠前面时也清掉, 别残留
            self._petting = False          # 睡着就别继续眯眼蹭头了
            self._pet_timer.stop()
        self._apply_opacity()              # 深睡半透明按“设置值与 0.55 取小”算
        self._relayout()
        self._rebuild_mask()
        self._update_breath()
        self.update()

    def _enter_sleep(self):
        """进浅睡: 有打哈欠图且动画开着时, 先打一个哈欠、约 1.2 秒后再真正睡。

        图缺失 / 动画关了 / 省电 / 正在吃东西或做动作时直接睡, 不崩。
        _on_logic 每秒都会调这里, _pending_sleep 守着不重复开哈欠。
        """
        if self._sleeping() or self._pending_sleep:
            return
        if (self.pix_yawn and self.anim_enabled and not self.power_saver
                and not self._eating and not self._doing_action()):
            self._pending_sleep = True
            self._start_yawn(rounds=1)
            QTimer.singleShot(1200, self._finish_pending_sleep)
            return
        self._do_enter_sleep()

    def _finish_pending_sleep(self):
        """睡前哈欠的收尾: 1.2 秒到了, 没被摸过才真的睡; 期间被摸过就留在待机。

        先清 _pending_sleep 再判断: 哈欠这 1.2 秒里可能被全局空闲判定抢先
        推进深睡(_set_state 会停动作), 那时也不能让标志残留, 否则醒来后
        _enter_sleep 会被永久拦住。
        """
        if not self._pending_sleep:
            return
        self._pending_sleep = False
        if self._sleeping():
            return
        if time.monotonic() - self.last_interact < self.sleep_minutes * 60:
            return
        self._stop_action()
        self._do_enter_sleep()

    def _do_enter_sleep(self):
        if self._sleeping():
            return
        self._set_state("sleep")
        self._say(self._pick(LINES_SLEEP), category="sleep")

    def _enter_deep_sleep(self):
        if self.state == "deep_sleep":
            return
        self._set_state("deep_sleep")

    def _wake_up(self, by_input: bool = False):
        if not self._sleeping():
            self.last_interact = time.monotonic()
            return
        was_deep = self.state == "deep_sleep"
        self.last_interact = time.monotonic()
        self._set_state("idle")
        self._bump_stat("wakes")
        self._schedule_walk(60, 120)          # 睡醒后继续安排散步(修复卡住不走的 bug)
        if by_input and was_deep:
            # 深睡醒来: 35% 伸懒腰, 35% 说回来了, 30% 什么也不说 ——
            # 一次只抽一个, 伸懒腰和 BACK 台词不同时出现。
            roll = random.random()
            if roll < WAKE_STRETCH_CHANCE:
                if not self._start_stretch():
                    self._say(self._pick(LINES_BACK), category="back")
            elif roll < WAKE_STRETCH_CHANCE + 0.35:
                self._say(self._pick(LINES_BACK), category="back")
        if self._water_pending:
            self._water_pending = False
            QTimer.singleShot(1500, lambda: self._say("对啦, 刚才叫你喝水来着～现在去倒杯水吧", category="water"))

    def _touch(self):
        """任何鼠标交互都算被摸过。"""
        self.last_interact = time.monotonic()
        if self._sleeping():
            self._wake_up()

    # ------------------------------------------------------------- 定时器
    def _on_logic(self):
        """1 秒一次: 睡眠判定 + 气泡跟随 + 第一期: 免打扰/定时提醒/应用/跟随模式。"""
        try:
            idle = get_global_idle_seconds()
            if idle >= 0:
                if self.state != "deep_sleep" and idle >= self.deep_sleep_min * 60:
                    self._enter_deep_sleep()
                elif self.state == "deep_sleep" and idle < 1.0:
                    self._wake_up(by_input=True)
            if not self._sleeping() and not self._jumping:
                # 用单调时钟算间隔: 系统时间被 NTP 校准/手动改时不会误判
                if time.monotonic() - self.last_interact >= self.sleep_minutes * 60:
                    self._enter_sleep()
            # 待机久了随机伸懒腰/打哈欠(节流 + 互斥都在 _maybe_idle_action 里)
            if not self._sleeping() and not self._jumping:
                self._maybe_idle_action()
            # 分辨率变化 / 拔掉副屏 / 任务栏挪位后, 别把猫留在屏幕外
            if not self._visible_somewhere(self.pos()):
                self.move(self._clamp_pos(self.pos()))
            self.bubble.follow(self.pos(), self.size(), self._content_top())
            # ---- 第一期 ----
            self._check_dnd_flush()
            self._check_reminders()
            self._poll_foreground_app(idle)
            self._follow_mouse_tick()
            self._rollover_stats_if_needed()
        except Exception:
            self._log_error("logic")

    def _on_frame(self):
        """~30fps: 眨眼 / 甩尾 / Zzz 相位; 只在真有变化时重绘。"""
        try:
            changed = False
            # 帧变了(眨眼 / 炸毛结束 / 被拎起…)但 window mask 还是上一帧的,
            # 会把新帧的耳朵、尾巴裁掉 —— 每 tick 比对, 不一致立刻重建。
            current = self._frame()[0]
            if current != (self._mask_key[0] if self._mask_key else None):
                self._rebuild_mask()
                changed = True
            if self._sleeping():
                # Zzz 飘得慢, 隔帧推进就够; 睡觉时把重绘频率砍半
                self._zzz_divider ^= 1
                if self._zzz_divider:
                    step = 0.005 if self.state == "deep_sleep" else 0.012
                    self.zzz_phase = (self.zzz_phase + step) % 1.0
                    changed = True
            else:
                self._blink_left -= 1
                if self._blink_left <= 0:
                    self.blink = not self.blink
                    self._blink_left = 4 if self.blink else random.randint(60, 180)
                    changed = True
                self._tail_left -= 1
                if self._tail_left <= 0:
                    self.tail_dx = random.choice((-2, 0, 2))
                    self._tail_left = random.randint(150, 400)
                    changed = True
            if changed:
                self.update()
        except Exception:
            self._log_error("frame")

    def _apply_runtime(self):
        """动画/省电开关变化后统一收敛: 定时器、比例、mask、呼吸。"""
        live = self.anim_enabled and not self.power_saver
        if live:
            if not self._frame_timer.isActive():
                self._frame_timer.start(FRAME_MS)
            # 关过动画又打开时, 下一次走动可能已被取消 —— 补排一次
            if not self._next_walk.isActive() and not self._sleeping() and not self._walk_timer.isActive():
                self._schedule_walk(60, 120)
        else:
            self._frame_timer.stop()
            self.blink = False
            self.tail_dx = 0
            self._stop_walk()
            self._stop_action()            # 关动画/省电: 伸懒腰/打哈欠做到一半也要停, 别卡住
        self.breath_group.stop()
        self._dy = 0
        self._relayout()
        self._rebuild_mask()
        self._update_breath()
        self.update()

    # ------------------------------------------------------------- 呼吸
    def _has_breath_frames(self) -> bool:
        """当前状态有没有真实呼吸帧: 有就用帧循环, 不再叠加 ±3px 程序化呼吸。"""
        if self.state in ("sleep", "deep_sleep"):
            return bool(self.pix_sleep_breath)
        if self.state == "idle":
            return bool(self.pix_idle_breath)
        return False

    def _breath_pool(self):
        """当前状态的呼吸循环帧池: [基础帧, 吸气, 呼气]。

        待机朝左时用翻转版(基础帧用 pix_idle_left, 否则退回正向);
        睡觉不分朝向, 沿用 pix_sleep。图缺了池子里只剩基础帧,
        _frame() 自然退回单张图, 不循环也不崩。
        """
        if self.state in ("sleep", "deep_sleep"):
            base = self.pix_sleep or self.pix_idle
            return [p for p in [base] + self.pix_sleep_breath if p is not None]
        if self.state == "idle":
            if self.facing < 0 and self.pix_idle_left is not None:
                base = self.pix_idle_left
                extra = self.pix_idle_breath_l or self.pix_idle_breath
            else:
                base = self.pix_idle
                extra = self.pix_idle_breath
            return [p for p in [base] + extra if p is not None]
        return []

    def _on_breath_tick(self):
        """呼吸帧推进: 每 BREATH_MS 一次, 守卫在 phase1.should_advance_breath 里。

        只推下标不直接画: _on_frame 每 33ms 会比对帧名, 变了就重建 mask 并重绘。
        """
        try:
            if not should_advance_breath(self.state, self.anim_enabled,
                                           self.power_saver, self._eating,
                                           self._doing_action(), self._petting,
                                           self._holding, self._jumping):
                return
            new_index = next_breath_index(self._breath_index, len(self._breath_pool()))
            if new_index != self._breath_index:
                self._breath_index = new_index
                self._relayout()
                self._rebuild_mask()
                self.update()
        except Exception:
            self._log_error("breath")

    def _update_breath(self):
        try:
            self.breath_group.stop()
            self._dy = 0
            if not (self.anim_enabled and not self.power_saver) or self._jumping:
                self.update()
                return
            # 有真实呼吸帧时不叠加程序化位移, 避免双重抖动
            if self._has_breath_frames():
                self.update()
                return
            duration = {"deep_sleep": 1800, "sleep": 1000}.get(self.state, 520)
            self.breath_up.setDuration(duration)
            self.breath_down.setDuration(duration)
            self.breath_group.start()
        except Exception:
            self._log_error("breath")

    # ------------------------------------------------------------- 走动
    def _schedule_walk(self, low: int = 60, high: int = 120):
        # 原地待着模式: 不排散步; 跟随鼠标模式: 走路由 _follow_mouse_tick 管
        if self.mode != "free":
            return
        self._next_walk.start(random.randint(low, high) * 1000)

    def _start_walk(self):
        if self.mode != "free":
            return
        if (self._sleeping() or self._jumping or self._petting
                or self._doing_action()
                or self.power_saver or not self.anim_enabled):
            self._schedule_walk(30, 60)
            return
        self.state = "walk"
        self._bump_stat("walks")
        self._walk_left = random.randint(90, 180)
        self._walk_total = self._walk_left                 # 缓动要用总步数
        self._walk_toggle = 0                                # 从头开始步态循环
        self._walk_screen = self._screen()      # 只缓存屏幕, 可用区域每帧重读(任务栏会动)
        self._walk_timer.start(FRAME_MS)
        self._relayout()                        # 万一 walk 帧尺寸和 idle 不同, 先适配
        self._rebuild_mask()
        self.update()
        self._say(self._pick(LINES_IDLE), category="idle")

    def _stop_walk(self):
        self._walk_timer.stop()
        self._next_walk.stop()          # 睡觉/关动画期间别再空转地排散步
        self._walk_left = 0
        self._walk_bob = 0
        if self.state == "walk":
            self.state = "idle"

    def _end_walk(self):
        self._walk_timer.stop()
        self._walk_left = 0
        self._walk_bob = 0
        if self.state == "walk":              # 别把 sleep / deep_sleep 覆盖成 idle
            self.state = "idle"
            self._relayout()                  # walk 帧和 idle 帧尺寸可能不同, 重新适配
            self._rebuild_mask()
            self.update()
        self._save_pos()
        self._schedule_walk(60, 120)

    def _on_walk_tick(self):
        try:
            if (self.state != "walk" or self._sleeping()
                    or not (self.anim_enabled and not self.power_saver)):
                self._end_walk()
                return
            self._walk_left -= 1
            # 每 _walk_step_ticks 帧切一次步态; 帧数越多间隔越短(总周期约 1 秒)
            if self._walk_left % self._walk_step_ticks == 0:
                self._walk_toggle += 1
                self._relayout()               # 两帧尺寸不同时也要跟着变(相同则是空操作)
                self._rebuild_mask()
                self.update()
            # 步态起伏按相位走正弦, 不再只有"抬起/落下"两档
            stride = max(1, self._walk_step_ticks)
            phase = (stride - (self._walk_left % stride)) / stride
            bob = -int(round(2.0 * abs(math.sin(math.pi * phase))))
            if bob != self._walk_bob:
                self._walk_bob = bob
                self.update()
            area = (self._walk_screen or self._screen()).availableGeometry()
            # 起步/收步缓动: 头尾各 14 帧降速到 1px, 中间 2px, 不会"啪"一下开始又停住
            done = max(0, self._walk_total - self._walk_left)
            ramp = min(1.0, done / 14.0, self._walk_left / 14.0)
            vx = max(1, int(round(2 * ramp))) if self._walk_total > 28 else 2
            x = self.x() + self.facing * vx
            if x <= area.left():
                x = area.left()
                self.facing = 1
                self.update()
            elif x + self.width() >= area.right():
                x = area.right() - self.width()
                self.facing = -1
                self.update()
            self.move(x, self.y())
            self.bubble.follow(self.pos(), self.size(), self._content_top())
            if self._walk_left <= 0:
                self._end_walk()
        except Exception:
            self._log_error("walk")

    # ------------------------------------------------------------- 提醒
    def _on_idle_talk(self):
        try:
            if self.state == "deep_sleep":
                pass
            elif self.state == "sleep":
                if random.random() < 0.5:
                    self._say(self._pick(LINES_SLEEP), category="sleep")
            elif not self.power_saver:
                self._say(self._pick(LINES_IDLE), category="idle")
        finally:
            self._idle_talk.start(next_idle_interval(self.speech_tier) * 1000)

    def _apply_idle_talk_interval(self):
        """语录频率改了: 下一次闲聊按新档位重排(正在等的这一次也重排)。"""
        self._idle_talk.stop()
        self._idle_talk.start(next_idle_interval(self.speech_tier) * 1000)

    def _apply_water_timer(self):
        self._water_timer.stop()
        if self.water_enabled:
            self._water_timer.start(max(1, self.water_interval) * 60 * 1000)

    def _on_water_remind(self):
        if self.water_enabled:
            self._bump_stat("reminders")
            if self.state == "deep_sleep":
                # 只有深睡(人明显不在)才攒着, 醒来补弹
                self._water_pending = True
            else:
                # 浅睡说明人就在电脑前, 只是没摸猫 —— 直接弹
                self._say(f"{PET_NAME}提醒你：该喝水啦～起来倒杯水，活动一下吧", category="water")
        self._apply_water_timer()

    # ------------------------------------------------- 第一期: 免打扰 / 定时提醒
    def start_dnd(self, minutes: int):
        """开始免打扰 N 分钟: 期间一句话都不说, 提醒攒着, 结束汇总一句。"""
        minutes = max(1, int(minutes))
        self._dnd.start(minutes)
        self.bubble.hide()

    def end_dnd(self):
        """提前结束免打扰: 有攒下的提醒就汇总一句。"""
        if not self._dnd.active() and not self._dnd.queued:
            return
        summary = self._dnd.flush_summary(PET_NAME)
        if summary:
            self._say(summary)

    def _check_dnd_flush(self):
        """免打扰时间到了: 自动汇总一句(没攒下东西就不说)。"""
        if self._dnd.until is not None and not self._dnd.active():
            summary = self._dnd.flush_summary(PET_NAME)
            if summary:
                self._say(summary)

    def _check_reminders(self):
        """定时提醒: 每秒看一次, 到点的说一句并记 last_fired, 写回 pet.json。"""
        if not self.reminders:
            return
        due = due_reminders(self.reminders, datetime.now())
        if not due:
            return
        self.store.set("reminders", self.reminders)
        for reminder in due:
            self._bump_stat("reminders")
            self._say(reminder["text"])

    def show_today_summary(self):
        """今日汇总: 只报本地统计, 没有余额, 不要编造余额。"""
        self._say(self._stats.summary_text(PET_NAME))

    def _rollover_stats_if_needed(self):
        """跨天: 今日统计清零(只清计数, 不动其他配置)。"""
        today = date.today().isoformat()
        if self._stats.date_str != today:
            self._stats = DailyStats(date_str=today)
            self._persist_stats()

    # ------------------------------------------------- 第一期: 跟随鼠标模式
    def _follow_mouse_tick(self):
        """跟随鼠标模式: 待机时慢慢向光标横向靠近(只动 x, 不动 y)。"""
        if self.mode != "follow":
            return
        if self._sleeping() or self._jumping or self._petting or self._dragging:
            return
        if not (self.anim_enabled and not self.power_saver):
            return
        try:
            cursor = QCursor.pos()
            area = self._screen().availableGeometry()
            if not area.contains(cursor):
                return
            target_x = cursor.x() - self.width() // 2
            dx = target_x - self.x()
            if abs(dx) < 4:
                return
            step = max(1, min(8, abs(dx)))
            self.facing = 1 if dx > 0 else -1
            self.move(self._clamp_pos(QPoint(self.x() + (step if dx > 0 else -step),
                                             self.y())))
            self.bubble.follow(self.pos(), self.size(), self._content_top())
        except Exception:
            self._log_error("follow")

    # ------------------------------------------------- 第一期: 应用联动
    def _rebuild_app_tracker(self):
        self._app_tracker = AppUsageTracker(self.app_watch)

    def _poll_foreground_app(self, idle_seconds: float):
        """每秒一次: 前台应用检测(仅 Windows, 其他平台直接返回)。"""
        if not self.app_watch and not self.app_launch_on:
            return
        if not platform_win.is_windows():
            return
        try:
            info = platform_win.get_foreground_app()
        except Exception:
            return
        if not info:
            return
        now = time.monotonic()
        events = self._app_tracker.update(info["exe"], now, idle_seconds,
                                          launch_on=self.app_launch_on)
        for event in events:
            if event["kind"] == "launch":
                line = self._pick(LINES_LAUNCH)
                if line:
                    self._say(line.replace("{app}", event["app"]))
            elif event["kind"] == "usage":
                self._bump_stat("reminders")
                self._say(f"{event['app']} 已经连续用了 {event['minutes']} 分钟啦，"
                          f"起来活动一下吧～")

    # ------------------------------------------------- 第一期: 天气(免 Key)
    def _check_weather(self, city: str = None):
        """后台线程查天气, 查到后气泡播报中文; 失败也播报一句, 不崩。"""
        if not self.weather_on and city is None:
            return
        city = (city or self.weather_city or "").strip()
        if not city:
            self._say("先在设置里填个城市，我才能帮你查天气哦～")
            return
        if self._weather_result is not None and not self._weather_result.get("done"):
            return  # 上一次还没查完, 别重复开线程
        self._weather_result = {"done": False, "city": city}

        def _run():
            try:
                info = fetch_weather(city)
                self._weather_result.update({"done": True, "info": info})
            except WeatherError as exc:
                self._weather_result.update({"done": True, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001  后台线程绝不能把异常带回 UI
                self._weather_result.update({"done": True, "error": f"天气查不到：{exc}"})

        threading.Thread(target=_run, daemon=True).start()

    def _on_weather_poll(self):
        result = self._weather_result
        if not result or not result.get("done"):
            return
        self._weather_result = None
        if result.get("info"):
            self._say(format_weather_line(result["city"], result["info"]))
        else:
            self._say(result.get("error") or "天气查不到，稍后再试试吧")

    # ------------------------------------------------- 第一期: 窗口(层级/透明度/穿透/自启/大小)
    def _apply_layer(self, silent: bool = False):
        """层级: top 置顶 / normal 普通 / bottom 置底。"""
        flags = self.windowFlags()
        flags &= ~(Qt.WindowStaysOnTopHint | Qt.WindowStaysOnBottomHint)
        if self.window_layer == "top":
            flags |= Qt.WindowStaysOnTopHint
        elif self.window_layer == "bottom":
            flags |= Qt.WindowStaysOnBottomHint
        self.always_on_top = (self.window_layer == "top")
        self.store.set("ontop", self.always_on_top)
        pos = self.pos()
        self.setWindowFlags(flags)            # 会重建原生窗口
        self.move(pos)
        self.show()
        self._rebuild_mask()
        if not silent:
            self.update()

    def _apply_on_top(self, silent: bool = False):
        # 兼容旧调用: 以 always_on_top 为准同步一次层级(置底时不动, 避免互相打架)。
        if self.window_layer != "bottom":
            self.window_layer = "top" if self.always_on_top else "normal"
        self._apply_layer(silent=silent)

    def _apply_opacity(self):
        """透明度: 深睡时取 min(设置值, 0.55), 其他状态直接用设置值。"""
        target = self.opacity if self.state != "deep_sleep" else min(self.opacity, 0.55)
        self.setWindowOpacity(target)

    def _apply_click_through(self):
        """鼠标穿透: 开了就点不中猫(从托盘右键解除); 关了恢复正常。"""
        flags = self.windowFlags()
        if self.click_through:
            flags |= Qt.WindowTransparentForInput
        else:
            flags &= ~Qt.WindowTransparentForInput
        pos = self.pos()
        self.setWindowFlags(flags)
        self.move(pos)
        self.show()
        self._rebuild_mask()

    def _apply_autostart(self):
        """开机自启: 仅 Windows 写注册表 Run 键; 其他平台只记配置, 不报错。"""
        if not platform_win.is_windows():
            return
        ok = platform_win.set_autostart(self.autostart)
        if not ok and self.autostart:
            print("[pet] 开机自启写注册表失败, 已忽略", file=sys.stderr)

    def _apply_size_preset(self, name: str):
        """大小档位: 按档位设缩放, 保持中心不动。"""
        scale = SIZE_PRESETS.get(name)
        if scale is None:
            return
        self.scale = scale
        self.size_preset = name
        self.store.set("scale_val", self.scale)
        center = self.geometry().center()
        self._relayout()
        self.move(center - self.rect().center())
        self._rebuild_mask()
        self.update()

    # ------------------------------------------------- 第一期: 全局快捷键
    def showEvent(self, event):
        # 窗口第一次显示出来才有原生句柄, 这时再注册全局快捷键。
        super().showEvent(event)
        if not self._hotkeys_registered:
            self._hotkeys_registered = True
            self._apply_hotkeys()

    def _apply_hotkeys(self):
        """(重)注册全局快捷键: 仅 Windows; 其他平台直接跳过, 不报错。"""
        if not platform_win.is_windows():
            return
        try:
            hwnd = int(self.winId())
        except Exception:
            return
        for hotkey_id in list(self._hotkey_map):
            platform_win.unregister_hotkey(hwnd, hotkey_id)
        self._hotkey_map = {}
        from .phase1 import hotkey_to_windows
        for index, item in enumerate(self.hotkeys or []):
            if not isinstance(item, dict) or not item.get("enabled", True):
                continue
            try:
                mods, vk = hotkey_to_windows(str(item.get("key", "")))
            except ValueError:
                continue
            hotkey_id = 0xA000 + index
            if platform_win.register_hotkey(hwnd, hotkey_id, mods, vk):
                self._hotkey_map[hotkey_id] = item

    def _unregister_hotkeys(self):
        if not platform_win.is_windows() or not self._hotkey_map:
            return
        try:
            hwnd = int(self.winId())
        except Exception:
            return
        for hotkey_id in list(self._hotkey_map):
            platform_win.unregister_hotkey(hwnd, hotkey_id)
        self._hotkey_map = {}

    def nativeEvent(self, eventType, message):
        if platform_win.is_windows() and eventType == b"windows_generic_MSG":
            try:
                import ctypes
                from ctypes import wintypes

                class MSG(ctypes.Structure):
                    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                                ("time", wintypes.DWORD), ("pt", wintypes.POINT)]

                msg = ctypes.cast(int(message), ctypes.POINTER(MSG)).contents
                if msg.message == platform_win.WM_HOTKEY:
                    self._on_hotkey(msg.wParam)
                    return True, 0
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    def _on_hotkey(self, hotkey_id: int):
        item = self._hotkey_map.get(hotkey_id)
        if not item:
            return
        kind = str(item.get("kind", ""))
        custom = str(item.get("text", "")).strip()
        if kind == "custom" and custom:
            self._say(custom, category="hotkey")
            return
        pool = {"praise": LINES_PRAISE, "comfort": LINES_COMFORT,
                "tease": LINES_TEASE}.get(kind)
        if pool:
            self._say(self._pick(pool), category="hotkey")

    # ------------------------------------------------------------- 纪念日
    def _startup_line(self):
        if self._missing_assets:
            self._say(f"我是{PET_NAME}，不过没找到 {IMG_IDLE} 这些图，先用简笔画代替～")
            return
        self._say(f"我是{PET_NAME}，今天也陪着你～")
        if self.anni_name and self.anni_date:
            QTimer.singleShot(3800, self._daily_greeting)

    def _daily_greeting(self):
        if not self.anni_name or not self.anni_date:
            return
        today = date.today().isoformat()
        if self.store.get_str("last_anni_shown") == today:
            return
        try:
            days = (date.fromisoformat(self.anni_date) - date.today()).days
        except ValueError:
            return
        self.store.set("last_anni_shown", today)
        if days > 0:
            self._say(f"距离「{self.anni_name}」还有 {days} 天～", category="anni")
        elif days == 0:
            self._say(f"今天就是「{self.anni_name}」!", category="anni")
        else:
            self._say(f"「{self.anni_name}」已经过去 {-days} 天啦", category="anni")

    def _maybe_show_anni(self):
        if not self.anni_name or not self.anni_date:
            return
        try:
            days = (date.fromisoformat(self.anni_date) - date.today()).days
        except ValueError:
            return
        if days > 0:
            self._say(f"偷偷告诉你, 再过 {days} 天就是{self.anni_name}啦", category="anni")
        elif days == 0:
            self._say(f"今天就是{self.anni_name}！要开心哦", category="anni")

    # ------------------------------------------------------------- 交互
    def enterEvent(self, event):
        # 悬停只负责"叫醒", 不当成交互计时: 否则鼠标停在猫身上时它永远不睡
        if self.state == "sleep":
            self._wake_up()
        super().enterEvent(event)

    def mousePressEvent(self, event):
        self._touch()
        if event.button() == Qt.LeftButton:
            self._drag_off = event.globalPos() - self.frameGeometry().topLeft()
            self._press_pos = event.globalPos()
            self._dragging = False
        elif event.button() == Qt.RightButton:
            self._show_menu(event.globalPos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.locked:
            # 锁定位置: 拖不动, 但单击/右键照旧(在 mouseReleaseEvent 里处理)
            super().mouseMoveEvent(event)
            return
        if self._drag_off is not None and event.buttons() & Qt.LeftButton:
            if (event.globalPos() - self._press_pos).manhattanLength() > 5:
                if not self._dragging and self.pix_hold is not None:
                    self._holding = True          # 切到"被拎起", 有真实反馈
                    self._relayout()
                    self._rebuild_mask()
                    self.update()
                self._dragging = True
            self.move(self._clamp_pos(event.globalPos() - self._drag_off))
            self.bubble.follow(self.pos(), self.size(), self._content_top())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_off is not None:
            self._drag_off = None
            if self._holding:                     # 松手放回地上
                self._holding = False
                self._relayout()
                self._rebuild_mask()
                self.update()
            if not self._dragging:
                self._on_click()
            else:
                self._snap_to_edge()
                self._save_pos()
                if random.random() < 0.3:
                    self._say(self._pick(LINES_DRAG), category="drag")
        super().mouseReleaseEvent(event)

    def _snap_to_edge(self):
        """拖拽松手后吸附到屏幕四边(24px 内就贴过去)。"""
        area = self._screen().availableGeometry()
        x, y = snap_position(self.x(), self.y(), self.width(), self.height(),
                             (area.left(), area.top(), area.right(), area.bottom()))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return
        target = max(SCALE_MIN, min(self.scale * (1.12 if steps > 0 else 0.89), SCALE_MAX))
        if abs(target - self.scale) < 0.005:
            return
        self.scale = round(target, 3)
        self.size_preset = ""              # 滚轮无级调后不再是某个档位
        self.store.set("scale_val", self.scale)
        self.store.set("size_preset", "")
        center = self.geometry().center()
        self._relayout()
        self.move(center - self.rect().center())
        self._rebuild_mask()
        self.update()

    def _on_click(self):
        """单击 = 被摸(有 cat_pet.png 时); 500ms 内再点 = 连戳, 炸毛跳 + 抖一抖。"""
        self._bump_stat("clicks")
        now = time.monotonic()
        rapid = (now - self._last_click_at) < DOUBLE_CLICK_SEC
        self._last_click_at = now
        if rapid:
            self._end_pet()                       # 取消被摸, 换成炸毛
            self._start_angry()                   # 炸毛表情 + 窗口抖动(保证看得见)
            self._jump()
            self._say(self._pick(LINES_ANGRY), category="pet")
            return
        if self.pix_pet is not None and not self._jumping:
            self._start_pet()
            self._say(self._pick(LINES_CLICK), category="pet")
            return
        self._jump()                              # 没被摸帧: 退回老行为
        self._say(self._pick(LINES_CLICK), category="pet")

    # ------------------------------------------------------------- 被摸
    def _start_pet(self):
        if self.state == "walk":
            self._end_walk()                     # 别一边眯眼一边平移
        self._petting = True
        self._relayout()                         # 被摸帧尺寸和 idle 可能不同
        self._rebuild_mask()
        self.update()
        self._pet_timer.start(PET_MS)

    def _end_pet(self):
        if not self._petting:
            return
        self._petting = False
        self._pet_timer.stop()
        self._relayout()
        self._rebuild_mask()
        self.update()

    def _jump(self):
        self._jumping = True
        self.clearMask()                      # 跳跃会移出静态 mask, 先取消避免被裁
        self.breath_group.stop()
        self.jump_up.setStartValue(self._dy)
        self.jump_group.stop()
        self.jump_group.start()
        if random.random() < 0.1:
            QTimer.singleShot(1200, self._maybe_show_anni)

    # ------------------------------------------------------------- 炸毛
    def _start_angry(self, hold_ms: int = 900):
        """连戳: 炸毛表情保持 + 窗口左右抖动, 保证看得见。"""
        self._angry_until = time.monotonic() + hold_ms / 1000.0
        self._shake()
        self._relayout()
        self._rebuild_mask()
        self.update()

    def _shake(self, steps: int = 8, amp: int = 3):
        self._shake_base = self.pos()
        self._shake_left = steps
        if not hasattr(self, "_shake_timer"):
            self._shake_timer = QTimer(self, timeout=self._on_shake_tick)
        self._shake_timer.start(35)

    def _on_shake_tick(self):
        try:
            self._shake_left -= 1
            if self._shake_left <= 0:
                self._shake_timer.stop()
                self.move(self._shake_base)
                return
            dx = 3 if self._shake_left % 2 else -3
            self.move(self._shake_base.x() + dx, self._shake_base.y())
        except Exception:
            self._log_error("shake")

    def _on_jump_finished(self):
        self._jumping = False
        self._dy = 0
        self._rebuild_mask()
        self._update_breath()
        self.update()

    # ------------------------------------------------------------- 菜单
    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#fff;border:1px solid #ddd;border-radius:8px;padding:6px;}"
            "QMenu::item{padding:6px 22px;border-radius:4px;}"
            "QMenu::item:checked{background:#ffe9d6;}"
            "QMenu::item:selected{background:#ffe9d6;}"
        )
        self._act_top = QAction("始终置顶", menu, checkable=True)
        self._act_top.triggered.connect(self._toggle_on_top)
        menu.addAction(self._act_top)

        self._act_anim = QAction("动画开关", menu, checkable=True)
        self._act_anim.triggered.connect(self._toggle_anim)
        menu.addAction(self._act_anim)

        self._act_power = QAction("省电模式", menu, checkable=True)
        self._act_power.triggered.connect(self._toggle_power)
        menu.addAction(self._act_power)

        menu.addSeparator()
        # ---- 第一期: 模式(单选) ----
        menu_mode = menu.addMenu("模式")
        self._mode_group = QActionGroup(menu_mode)
        self._mode_group.setExclusive(True)
        self._mode_actions = {}
        from .phase1 import MODE_NAMES
        for key in MODES:
            act = QAction(MODE_NAMES[key], menu_mode, checkable=True)
            act.setData(key)
            act.triggered.connect(lambda _c, k=key: self._set_mode(k))
            self._mode_group.addAction(act)
            menu_mode.addAction(act)
            self._mode_actions[key] = act

        # ---- 第一期: 语录频率(单选) ----
        menu_tier = menu.addMenu("语录频率")
        self._tier_group = QActionGroup(menu_tier)
        self._tier_group.setExclusive(True)
        self._tier_actions = {}
        from .phase1 import SPEECH_TIER_NAMES
        for key in ("quiet", "normal", "chatty", "talkative"):
            act = QAction(SPEECH_TIER_NAMES[key], menu_tier, checkable=True)
            act.setData(key)
            act.triggered.connect(lambda _c, k=key: self._set_speech_tier(k))
            self._tier_group.addAction(act)
            menu_tier.addAction(act)
            self._tier_actions[key] = act

        # ---- 第一期: 大小档位(单选, 另有滚轮无级调) ----
        menu_size = menu.addMenu("大小")
        self._size_group = QActionGroup(menu_size)
        self._size_group.setExclusive(True)
        self._size_actions = {}
        for name in SIZE_PRESETS:
            act = QAction(f"{name}（{SIZE_PRESETS[name]}×）", menu_size, checkable=True)
            act.setData(name)
            act.triggered.connect(lambda _c, n=name: self._apply_size_preset(n))
            self._size_group.addAction(act)
            menu_size.addAction(act)
            self._size_actions[name] = act

        # ---- 第一期: 层级(单选) ----
        menu_layer = menu.addMenu("窗口层级")
        self._layer_group = QActionGroup(menu_layer)
        self._layer_group.setExclusive(True)
        self._layer_actions = {}
        from .phase1 import LAYER_NAMES
        for key in LAYERS:
            act = QAction(LAYER_NAMES[key], menu_layer, checkable=True)
            act.setData(key)
            act.triggered.connect(lambda _c, k=key: self._set_layer(k))
            self._layer_group.addAction(act)
            menu_layer.addAction(act)
            self._layer_actions[key] = act

        self._act_locked = QAction("锁定位置（拖不动）", menu, checkable=True)
        self._act_locked.triggered.connect(self._toggle_locked)
        menu.addAction(self._act_locked)

        self._act_through = QAction("鼠标穿透", menu, checkable=True)
        self._act_through.triggered.connect(self._toggle_click_through)
        menu.addAction(self._act_through)

        self._act_autostart = QAction("开机自启", menu, checkable=True)
        self._act_autostart.triggered.connect(self._toggle_autostart)
        menu.addAction(self._act_autostart)

        menu.addSeparator()
        act_weather = QAction("查看天气", menu)
        act_weather.triggered.connect(lambda: self._check_weather(city=self.weather_city))
        menu.addAction(act_weather)

        act_summary = QAction("今日汇总", menu)
        act_summary.triggered.connect(self.show_today_summary)
        menu.addAction(act_summary)

        # ---- 第一期: 免打扰 ----
        menu_dnd = menu.addMenu("免打扰")
        for minutes in (15, 30, 60, 120):
            act = QAction(f"免打扰 {minutes} 分钟", menu_dnd)
            act.triggered.connect(lambda _c, m=minutes: self.start_dnd(m))
            menu_dnd.addAction(act)
        self._act_dnd_end = QAction("结束免打扰", menu_dnd)
        self._act_dnd_end.triggered.connect(self.end_dnd)
        menu_dnd.addAction(self._act_dnd_end)

        menu.addSeparator()
        act_settings = QAction("设置…", menu)
        act_settings.triggered.connect(self._open_settings)
        menu.addAction(act_settings)

        act_reset = QAction("重置窗口位置与透明效果", menu)
        act_reset.triggered.connect(self._reset_window)
        menu.addAction(act_reset)

        act_feed = QAction(f"喂{PET_NAME}吃小零食", menu)
        act_feed.triggered.connect(self._feed)
        menu.addAction(act_feed)

        # ---- 新动作: 手动入口(图缺了就禁用, 做动作/吃东西时也禁用) ----
        self._act_stretch = QAction("伸个懒腰", menu)
        self._act_stretch.triggered.connect(self._manual_stretch)
        menu.addAction(self._act_stretch)

        self._act_yawn = QAction("打个哈欠", menu)
        self._act_yawn.triggered.connect(self._manual_yawn)
        menu.addAction(self._act_yawn)

        menu.addSeparator()
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.shutdown)
        menu.addAction(act_quit)
        return menu

    def _show_menu(self, global_pos: QPoint):
        if self._menu is None:                # 只建一次, 不再每次右键泄漏
            self._menu = self._build_menu()
        self._act_top.setChecked(self.always_on_top)
        self._act_anim.setChecked(self.anim_enabled)
        self._act_power.setChecked(self.power_saver)
        self._act_locked.setChecked(self.locked)
        self._act_through.setChecked(self.click_through)
        self._act_autostart.setChecked(self.autostart)
        for key, act in self._mode_actions.items():
            act.setChecked(key == self.mode)
        for key, act in self._tier_actions.items():
            act.setChecked(key == self.speech_tier)
        for name, act in self._size_actions.items():
            act.setChecked(name == self.size_preset)
        for key, act in self._layer_actions.items():
            act.setChecked(key == self.window_layer)
        # 新动作入口: 图缺了 / 正在吃东西或做动作时禁用, 点了也不会重复触发
        busy = self._eating or self._doing_action()
        self._act_stretch.setEnabled(bool(self.pix_stretch) and not busy)
        self._act_yawn.setEnabled(bool(self.pix_yawn) and not busy)
        self._menu.exec_(global_pos)

    # ---- 第一期: 菜单里的小开关 ----
    def _set_mode(self, mode: str):
        if mode not in MODES:
            return
        self.mode = mode
        self.store.set("mode", mode)
        self._stop_walk()                     # 换模式先停下当前散步, 再按新模式重排
        self._apply_runtime()

    def _set_speech_tier(self, tier: str):
        if tier not in ("quiet", "normal", "chatty", "talkative"):
            return
        self.speech_tier = tier
        self.store.set("speech_tier", tier)
        self._apply_idle_talk_interval()

    def _set_layer(self, layer: str):
        if layer not in LAYERS:
            return
        self.window_layer = layer
        self.store.set("window_layer", layer)
        self._apply_layer()

    def _toggle_locked(self, checked: bool):
        self.locked = checked
        self.store.set("locked", checked)

    def _toggle_click_through(self, checked: bool):
        self.click_through = checked
        self.store.set("click_through", checked)
        self._apply_click_through()

    def _toggle_autostart(self, checked: bool):
        self.autostart = checked
        self.store.set("autostart", checked)
        self._apply_autostart()

    def _feed(self):
        self._touch()
        self._bump_stat("feeds")
        self._start_eat()
        self._say(self._pick(LINES_FEED), category="feed")
        if random.random() < 0.5:
            QTimer.singleShot(900, lambda: self._say(self._pick(LINES_FEED), category="feed"))

    def _start_eat(self, rounds: int = 3):
        """喂零食: 张嘴/闭嘴两帧循环 rounds 轮, 约 0.7 秒后回待机。"""
        if not self.pix_eat:
            return
        if self._doing_action():           # 正在伸懒腰/打哈欠时不抢着吃
            return
        self._eating = True
        self._eat_index = 0
        self._eat_left = max(2, rounds * len(self.pix_eat))
        self._relayout()
        self._rebuild_mask()
        self.update()
        self._eat_timer.start(EAT_MS)

    def _on_eat_tick(self):
        try:
            self._eat_left -= 1
            if self._eat_left <= 0:
                self._eat_timer.stop()
                self._eating = False
                self._eat_index = 0
                self._relayout()
                self._rebuild_mask()
                self.update()
                return
            self._eat_index += 1
            self._relayout()
            self._rebuild_mask()
            self.update()
        except Exception:
            self._log_error("eat")

    # ------------------------------------------------- 新动作: 伸懒腰 / 打哈欠
    def _doing_action(self) -> bool:
        """正在做伸懒腰或打哈欠(共用一个定时器, 同一时间只可能做一个)。"""
        return getattr(self, "_action_kind", None) is not None

    def _stop_action(self):
        """停掉新动作并清状态: 入睡/关动画/省电/重置/退出时调用, 别卡住。"""
        timer = getattr(self, "_action_timer", None)
        if timer is not None:
            timer.stop()
        self._action_kind = None
        self._action_index = 0
        self._action_left = 0

    def _start_action(self, kind: str, lines, rounds: int = 2) -> bool:
        """新动作的通用入口, 仿照 _start_eat: 两帧循环 rounds 轮后回待机。

        返回 False 表示没做成(图缺失 / 正在吃东西或做别的动作 / 睡觉 /
        跳跃 / 被摸 / 被拎起 / 拖拽 / 动画关了 / 省电 / 不在待机状态),
        调用方据此决定要不要说兜底台词, 不要在图缺时崩。
        """
        pool = self.pix_stretch if kind == "stretch" else self.pix_yawn
        if not pool:
            return False
        if (self._eating or self._doing_action() or self._sleeping()
                or self._jumping or self._petting or self._holding or self._dragging
                or not self.anim_enabled or self.power_saver or self.state != "idle"):
            return False
        self._action_kind = kind
        self._action_index = 0
        self._action_left = max(2, rounds * len(pool))
        self._last_action_at = time.monotonic()
        self._relayout()
        self._rebuild_mask()
        self.update()
        self._action_timer.start(ACTION_MS)
        if lines:
            self._say(self._pick(lines), category="idle")
        return True

    def _start_stretch(self, rounds: int = 2) -> bool:
        """伸懒腰: 蹲着准备 -> 前爪前伸, 各播 rounds 轮。"""
        return self._start_action("stretch", LINES_STRETCH, rounds)

    def _start_yawn(self, rounds: int = 2) -> bool:
        """打哈欠: 小口微张 -> 大口捂嘴, 各播 rounds 轮。"""
        return self._start_action("yawn", LINES_YAWN, rounds)

    def _on_action_tick(self):
        """新动作的帧推进, 仿照 _on_eat_tick: 播完回待机并重建 mask。"""
        try:
            self._action_left -= 1
            if self._action_left <= 0:
                self._action_timer.stop()
                self._action_kind = None
                self._action_index = 0
                self._relayout()
                self._rebuild_mask()
                self.update()
                return
            self._action_index += 1
            self._relayout()
            self._rebuild_mask()
            self.update()
        except Exception:
            self._log_error("action")

    def _maybe_idle_action(self):
        """待机随机动作: 仅 state==idle 且什么都没在做时才考虑触发。

        节流与概率判断在 phase1.should_trigger_idle_action 里(纯函数,
        可单元测试); 这里只做状态守卫和二选一, 选中的动作图缺了就换另一个。
        """
        if (self.state != "idle" or self._eating or self._doing_action()
                or self._petting or self._holding or self._dragging
                or self._pending_sleep):
            return
        if not self.anim_enabled or self.power_saver:
            return
        if not self.pix_stretch and not self.pix_yawn:
            return
        idle_for = time.monotonic() - self.last_interact
        since_action = time.monotonic() - self._last_action_at
        if not should_trigger_idle_action(idle_for, since_action, random.random()):
            return
        kind = pick_idle_action(random.random())
        if kind == "stretch" and not self.pix_stretch:
            kind = "yawn"
        if kind == "yawn" and not self.pix_yawn:
            kind = "stretch"
        if kind == "stretch":
            self._start_stretch()
        else:
            self._start_yawn()

    def _manual_stretch(self):
        """右键菜单手动入口: 伸个懒腰。睡着时先叫醒, 走路时先停下。"""
        self._touch()
        if self.state == "walk":
            self._stop_walk()
        self._start_stretch()

    def _manual_yawn(self):
        """右键菜单手动入口: 打个哈欠。睡着时先叫醒, 走路时先停下。"""
        self._touch()
        if self.state == "walk":
            self._stop_walk()
        self._start_yawn()

    def _toggle_on_top(self, checked: bool):
        self.always_on_top = checked
        self.window_layer = "top" if checked else "normal"
        self.store.set("ontop", checked)
        self.store.set("window_layer", self.window_layer)
        self._apply_layer()

    def _toggle_anim(self, checked: bool):
        self.anim_enabled = checked
        self.store.set("anim_on", checked)
        self._apply_runtime()

    def _toggle_power(self, checked: bool):
        self.power_saver = checked
        self.store.set("power_saver", checked)
        self._apply_runtime()

    def _reset_window(self):
        self._mask_broken = False
        # 先清掉动画残留(眨眼/甩尾/Zzz 相位), 再交给 _set_state 统一收敛,
        # 否则重置完可能还保留着甩尾偏移 —— 也就是豆包说的"绕过了状态机"
        self.blink = False
        self.tail_dx = 0
        self.zzz_phase = 0.0
        self._petting = False
        self._pet_timer.stop()
        self._stop_action()
        self._pending_sleep = False        # 睡前哈欠做到一半也被重置掉, 别 1.2 秒后突然睡着
        self._set_state("idle")
        self.opacity = 1.0
        self.store.set("opacity", 1.0)
        self._apply_opacity()
        self._invalidate_masks()
        self.store.set("pos_x", -99999)       # 重置 = 真的回默认角落
        self.store.set("pos_y", -99999)
        self._place_initial()
        self._relayout()
        self._rebuild_mask()
        self._update_breath()
        self.update()
        self._say("我收拾好啦, 又精神了～")

    # ------------------------------------------------------------- 设置
    def _open_settings(self):
        # 统一设置窗口在 settings_ui.py(分页签); 这里只做委托, 避免 pet.py 继续膨胀。
        from .settings_ui import open_settings_dialog
        open_settings_dialog(self)

    # ------------------------------------------------------------- 绘制
    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            if self._mask_broken:
                painter.setBrush(QColor(255, 255, 255, 185))
                painter.setPen(QPen(QColor(220, 200, 180), 1))
                painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 16, 16)

            name, pm = self._frame()
            if pm is None:
                self._paint_fallback(painter)
            else:
                w, h = self._base_size(pm)
                # 有专门闭眼帧时 _frame() 已经换图了, 这里只处理"没有闭眼帧"的压扁式眨眼
                blink = (self.blink and self.anim_enabled and not self._sleeping()
                         and self.pix_blink is None)
                if blink:                     # 只压高度, 宽度不变, 底边对齐
                    h = max(1, int(round(h * BLINK_RATIO)))
                # 水平只居中一次(旧版这里和 _img_origin 重复居中, 换帧宽度不同就会横移)
                x = (self.width() - w) // 2 + (self.tail_dx if self.anim_enabled else 0)
                y = self.height() - h + self._dy + self._walk_bob
                painter.drawPixmap(QPoint(x, y), self._scaled(name, pm, w, h))

            if self._sleeping() and self.anim_enabled:
                self._paint_zzz(painter)
        except Exception:
            self._log_error("paint")
        finally:
            painter.end()

    def _zzz_font(self, size: int) -> QFont:
        font = self._font_cache.get(size)
        if font is None:
            font = QFont("Arial", size, QFont.Bold)
            self._font_cache[size] = font
        return font

    def _paint_zzz(self, painter: QPainter):
        """三个 Z: 位置固定(不漂移), 只做透明度渐隐 —— 这样 mask 能框得很紧。"""
        deep = self.state == "deep_sleep"
        painter.setRenderHint(QPainter.Antialiasing, True)
        for i in range(3):
            phase = (self.zzz_phase * (0.55 if deep else 1.0) + i * 0.33) % 1.0
            alpha = int((130 if deep else 235) * (1.0 - phase))
            if alpha <= 8:
                continue
            size = (8 if deep else 11) + i * 3
            x = self.width() - 54 + i * 6
            y = 42 - i * 15
            painter.setFont(self._zzz_font(size))
            painter.setPen(QColor(126, 138, 186, alpha))
            painter.drawText(QRectF(x, y, 22, 22), Qt.AlignCenter, "Z")

    def _paint_fallback(self, painter: QPainter):
        """图片缺失时的简笔画占位猫, 画在图片区域内(不含顶部留白)。"""
        w = self.width()
        h = self.height() - TOP_MARGIN
        top = TOP_MARGIN
        body = QColor(245, 238, 228)
        line = QColor(90, 70, 60)
        painter.setBrush(QBrush(body))
        painter.setPen(QPen(line, 2))
        painter.drawEllipse(QRectF(w * 0.20, top + h * 0.45, w * 0.60, h * 0.50))
        painter.drawEllipse(QRectF(w * 0.25, top + h * 0.08, w * 0.50, h * 0.42))
        painter.drawPolygon(
            QPoint(int(w * 0.30), int(top + h * 0.16)),
            QPoint(int(w * 0.26), int(top + h * 0.02)),
            QPoint(int(w * 0.40), int(top + h * 0.10)),
        )
        painter.drawPolygon(
            QPoint(int(w * 0.70), int(top + h * 0.16)),
            QPoint(int(w * 0.74), int(top + h * 0.02)),
            QPoint(int(w * 0.60), int(top + h * 0.10)),
        )
        painter.setBrush(QBrush(QColor(90, 150, 220)))
        painter.setPen(Qt.NoPen)
        ey = top + h * 0.28 + self._dy
        eye = 2 if (self.blink and self.anim_enabled) else 6
        painter.drawEllipse(QPoint(int(w * 0.40), int(ey)), eye, eye)
        painter.drawEllipse(QPoint(int(w * 0.60), int(ey)), eye, eye)
        painter.setBrush(QBrush(QColor(230, 150, 150)))
        painter.drawEllipse(QPoint(int(w * 0.50), int(top + h * 0.34 + self._dy)), 3, 2)

    # ------------------------------------------------------------- 杂项
    def _log_error(self, tag: str):
        """异常限流, 避免 30fps 刷爆日志。"""
        now = time.time()
        if now - self._err_time < 5:
            return
        self._err_time = now
        print(f"[pet] {tag} 异常:", file=sys.stderr)
        traceback.print_exc()

    def closeEvent(self, event):
        self.shutdown()
        event.accept()

    def shutdown(self):
        try:
            self._save_pos()
            self.store.sync()
        except Exception:
            pass
        for timer in (self._frame_timer, self._walk_timer, self._logic_timer,
                      self._idle_talk, self._next_walk, self._water_timer,
                      self._pet_timer, self._eat_timer, self._action_timer,
                      self._breath_timer, self._weather_timer):
            timer.stop()
        self._unregister_hotkeys()
        if getattr(self, "_shake_timer", None) is not None:   # 抖动是懒建的, 可能还没建
            self._shake_timer.stop()
        self.bubble.hide()
        QApplication.quit()

