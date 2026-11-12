# -*- coding: utf-8 -*-
"""桌面真实图标互动(Windows 专属, 冒险版): 读/推资源管理器桌面图标。

桌面图标列表是资源管理器进程里的 SysListView32, 读位置、读名字、
推位置都要跨进程: OpenProcess → 远程内存(VirtualAllocEx /
WriteProcessMemory / ReadProcessMemory) → SendMessageTimeoutW。
任何一步失败都返回安全空值(None / [] / False), 不崩、不弹错。

Win32 常量与结构已事先核对(见 SUMMARY.md):
    LVM_FIRST = 0x1000, LVM_GETITEMCOUNT = 0x1004,
    LVM_SETITEMPOSITION = 0x100F, LVM_GETITEMPOSITION = 0x1010,
    LVM_GETITEMTEXTW = 0x1073(0x1000 + 115), LVIF_TEXT = 0x0001。
    LVITEMW 字段顺序按 Windows SDK(Commctrl.h)写;
    LVM_SETITEMPOSITION 的 lParam 是打包坐标(低字 x, 高字 y), 不用远程内存;
    LVM_GETITEMPOSITION / LVM_GETITEMTEXTW 的 lParam 是远程内存里的
    POINT / LVITEMW 指针。

非 Windows 下整个模块静默关闭: IS_WINDOWS 为 False, 后端所有方法
返回空值, import 时不碰 ctypes.windll。测试时用同名方法的假后端
(内存 dict)替换, 不碰真 Explorer。

纯函数(可单元测试, 不依赖 Windows):
    clamp_icon_pos() / display_matches_file() / push_step() /
    walk_step_toward() / target_pet_x() / choose_icon() /
    restore_positions()
备份:
    IconLayoutBackup: 每个图标只记第一次推之前的原始位置, 内存 +
    JSON 文件双份; path 可注入, 测试时传临时目录。
"""

import json
import math
import os
import random
import sys

IS_WINDOWS = sys.platform == "win32"

# ================================ Win32 常量(已核对) ================================
# LVM_FIRST = 0x1000; 下面四个都是 LVM_FIRST + 偏移, 见模块 docstring。
LVM_GETITEMCOUNT = 0x1004       # 读图标个数, wParam=0, lParam=0
LVM_SETITEMPOSITION = 0x100F    # 推图标, wParam=下标, lParam=打包坐标(低字 x, 高字 y)
LVM_GETITEMPOSITION = 0x1010    # 读图标位置, wParam=下标, lParam=远程 POINT 指针
LVM_GETITEMTEXTW = 0x1073       # 读图标显示名, wParam=下标, lParam=远程 LVITEMW 指针
LVIF_TEXT = 0x0001              # LVITEM.mask: 只要文本

# 找桌面 ListView 时发给 Progman 的未公开消息(0x052C), 让资源管理器
# 建出放桌面图标的 WorkerW。
_WM_SPAWN_WORKER = 0x052C
# OpenProcess 权限: VM_OPERATION | VM_READ | VM_WRITE。
_PROCESS_VM_ACCESS = 0x0008 | 0x0010 | 0x0020
_MEM_COMMIT_RESERVE = 0x1000 | 0x2000
_PAGE_READWRITE = 0x04
_MEM_RELEASE = 0x8000
_SMTO_ABORTIFHUNG = 0x0002
_SEND_TIMEOUT_MS = 2000         # 跨进程消息超时, Explorer 卡住也不拖死界面
_TEXT_BUFFER_CHARS = 512        # 图标显示名最多读 512 个宽字符

# ================================ 玩法常量 ================================
PUSH_DISTANCE_PX = 32           # 每轮扒拉完推一小段
PUSH_JITTER_DEG = 30            # 推的方向随机偏一点(±30°), 别每次都一个方向
ICON_MARGIN_PX = 80             # 图标不能出屏幕: 留一个图标身位(约 80px)的边
WALK_ARRIVE_PX = 40             # 走到离目标 40px 内停下, 再开始扒拉
ICON_WIDTH_PX = 80              # 桌面图标格子宽(估算, 用来算图标中心 x)
ICON_BACKUP_FILENAME = "icon_layout_backup.json"


# ================================ 纯函数 ================================
def clamp_icon_pos(x, y, rect, margin=ICON_MARGIN_PX):
    """把图标位置夹在工作区里, 留一个图标身位的边, 图标不能出屏幕。

    rect = (left, top, right, bottom), right/bottom 是可用区域的右/下边。
    输入不对返回 (0, 0), 不崩。
    """
    try:
        x = int(x)
        y = int(y)
        left, top, right, bottom = (int(v) for v in rect)
        margin = int(margin)
    except (TypeError, ValueError):
        return (0, 0)
    x = max(left, min(x, right - margin))
    y = max(top, min(y, bottom - margin))
    return (x, y)


def display_matches_file(display, filename):
    """桌面图标显示名和文件夹里的文件名是不是同一个文件。

    忽略大小写; 快捷方式去掉 .lnk 后缀再比; 资源管理器藏扩展名时
    显示名可能没后缀(比如 "报告" 对 "报告.docx"), 也算。
    系统图标("此电脑""回收站")本来就对不上文件夹里的文件, 自然跳过。
    """
    if not isinstance(display, str) or not isinstance(filename, str):
        return False
    d = display.strip().lower()
    f = filename.strip().lower()
    if not d or not f:
        return False
    if d == f:
        return True
    if f.endswith(".lnk") and d == f[:-4]:
        return True
    base, _ext = os.path.splitext(f)
    if base and d == base:
        return True
    return False


def push_step(x, y, base_angle_deg, distance=PUSH_DISTANCE_PX,
              jitter_deg=PUSH_JITTER_DEG, rng=None):
    """从 (x, y) 出发, 沿 base_angle_deg 方向推 distance px, 方向随机
    偏一点(±jitter_deg)。角度是屏幕坐标: 0° = 向右, 90° = 向下。
    返回整数坐标; 输入不对返回 (0, 0), 不崩。rng 可注入(测试时给假的)。
    """
    try:
        x0 = float(x)
        y0 = float(y)
        base = float(base_angle_deg)
        dist = float(distance)
        jit = abs(float(jitter_deg))
    except (TypeError, ValueError):
        return (0, 0)
    r = rng if rng is not None else random
    try:
        angle = math.radians(base + r.uniform(-jit, jit))
    except (TypeError, ValueError, AttributeError):
        angle = math.radians(base)
    return (int(round(x0 + dist * math.cos(angle))),
            int(round(y0 + dist * math.sin(angle))))


def walk_step_toward(cur_x, target_x, step_px):
    """朝目标 x 走一步: 返回 (新 x, 是否到达)。

    离目标 40px 内就算到了(停下, 不用走到正下方); 一步跨过目标时
    停在目标上, 不冲过去。输入不对返回 (0, True), 让调用方停下。
    """
    try:
        cur = int(cur_x)
        tgt = int(target_x)
        step = max(1, int(step_px))
    except (TypeError, ValueError):
        return (0, True)
    if abs(tgt - cur) <= WALK_ARRIVE_PX:
        return (cur, True)
    move = step if tgt > cur else -step
    new = cur + move
    if (tgt > cur and new >= tgt) or (tgt < cur and new <= tgt):
        new = tgt
    return (new, abs(tgt - new) <= WALK_ARRIVE_PX)


def target_pet_x(icon_x, pet_width, area_left, area_right):
    """猫走到图标正下方: 猫左上角 x, 夹在可用区域里(猫不能出屏幕)。

    icon_x 是 LVM_GETITEMPOSITION 给的图标左上角 x(图标格子约 80px 宽,
    中心 x = icon_x + 40), 猫居中站到图标正下方。
    """
    try:
        center = int(icon_x) + ICON_WIDTH_PX // 2
        w = int(pet_width)
        left = int(area_left)
        right = int(area_right)
    except (TypeError, ValueError):
        return int(area_left) if isinstance(area_left, int) else 0
    return max(left, min(center - w // 2, right - w))


def choose_icon(icons, rng=None):
    """随机挑一个图标推(别总推同一个); 没图标返回 None。rng 可注入。"""
    if not icons:
        return None
    r = rng if rng is not None else random
    try:
        return r.choice(icons)
    except (TypeError, ValueError, IndexError, AttributeError):
        return None


def restore_positions(backend, backup, rect):
    """把备份里的图标逐个恢复到原始位置(先夹取, 再写)。

    返回 (成功数, 失败名列表); 单个图标失败就跳过, 不影响其他的。
    backend 只要有 set_position(name, x, y) 方法(真后端或假后端都行)。
    """
    ok = 0
    failed = []
    try:
        items = list(backup.items())
    except Exception:
        return (0, [])
    for name, pos in items:
        try:
            x, y = clamp_icon_pos(pos[0], pos[1], rect)
            if backend.set_position(name, x, y):
                ok += 1
            else:
                failed.append(name)
        except Exception:
            failed.append(name)
    return (ok, failed)


# ================================ 备份 ================================
class IconLayoutBackup:
    """图标原始位置备份: 每个图标只记第一次推之前的原始位置。

    内存 + JSON 文件双份; path 可注入(测试时传临时目录, 别往仓库里写)。
    文件缺失/损坏时当作空备份, 不崩。
    """

    def __init__(self, path):
        self.path = path
        self._items = {}
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for name, pos in data.items():
                    if isinstance(name, str) and isinstance(pos, (list, tuple)) \
                            and len(pos) == 2:
                        self._items[name] = (int(pos[0]), int(pos[1]))
        except (OSError, ValueError, TypeError):
            self._items = {}

    def save(self):
        """原子写: 先落 .tmp 再 replace, 写一半断电也不会把备份弄坏。"""
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({k: [v[0], v[1]] for k, v in self._items.items()},
                          f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def record(self, name, pos):
        """记下原始位置; 每个图标只记第一次, 已记过的不覆盖。
        返回 True 表示新记了一条。"""
        if not isinstance(name, str) or not name:
            return False
        if name in self._items:
            return False
        try:
            x, y = int(pos[0]), int(pos[1])
        except (TypeError, ValueError, IndexError):
            return False
        self._items[name] = (x, y)
        self.save()
        return True

    def get(self, name):
        return self._items.get(name)

    def items(self):
        return list(self._items.items())

    def __len__(self):
        return len(self._items)

    def __bool__(self):
        return bool(self._items)


    def retain(self, names):
        """仅保留恢复失败的条目，避免失败时丢失原始位置。

        先持久化剩余记录，再替换内存状态；写入失败时保留原备份。
        """
        remaining = {name: pos for name, pos in self._items.items()
                     if name in set(names)}
        if remaining == self._items:
            return True
        tmp = self.path + ".tmp"
        try:
            if remaining:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({k: [v[0], v[1]] for k, v in remaining.items()},
                              f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            else:
                os.remove(self.path)
        except FileNotFoundError:
            if remaining:
                return False
        except OSError:
            return False
        self._items = remaining
        return True

    def clear(self):
        """清空备份: 内存和文件一起清(恢复完调用)。"""
        self._items = {}
        for path in (self.path, self.path + ".tmp"):
            try:
                os.remove(path)
            except OSError:
                pass


# ================================ Win32 结构 ================================
# 只定义结构(标准库 ctypes, 哪里都能 import); 真正调 WinDLL 只在
# IS_WINDOWS 时发生, 非 Windows 下这些结构只是摆设。
import ctypes
from ctypes import wintypes


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _LVITEMW(ctypes.Structure):
    # 字段顺序按 Windows SDK(Commctrl.h)的 LVITEMW:
    # mask, iItem, iSubItem, state, stateMask, pszText, cchTextMax,
    # iImage, lParam, iIndent, iGroupId, cColumns, puColumns, piColFmt, iGroup。
    # 读显示名只用到 mask / iItem / iSubItem / pszText / cchTextMax,
    # 其余字段留着是为让结构大小和 SDK 一致。
    _fields_ = [
        ("mask", ctypes.c_uint),
        ("iItem", ctypes.c_int),
        ("iSubItem", ctypes.c_int),
        ("state", ctypes.c_uint),
        ("stateMask", ctypes.c_uint),
        ("pszText", ctypes.c_void_p),
        ("cchTextMax", ctypes.c_int),
        ("iImage", ctypes.c_int),
        ("lParam", ctypes.c_void_p),
        ("iIndent", ctypes.c_int),
        ("iGroupId", ctypes.c_int),
        ("cColumns", ctypes.c_uint),
        ("puColumns", ctypes.c_void_p),
        ("piColFmt", ctypes.c_void_p),
        ("iGroup", ctypes.c_int),
    ]


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


# FOLDERID_Desktop = {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
_FOLDERID_DESKTOP = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                          (0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41))


# ================================ 后端 ================================
class DesktopIconBackend:
    """桌面图标后端(仅 Windows): 通过 SysListView32 读/写图标位置。

    非 Windows 下所有方法返回安全空值, 不碰 ctypes.windll。
    """

    def __init__(self, desktop_dir=None):
        self.enabled = IS_WINDOWS
        self._desktop_dir_override = desktop_dir
        self._dlls = None

    # ---------- DLL 加载 ----------
    def _load(self):
        """懒加载 Win32 DLL 并设好 argtypes; 失败返回 None。"""
        if not self.enabled:
            return None
        if self._dlls is not None:
            return self._dlls
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            ole32 = ctypes.WinDLL("ole32", use_last_error=True)

            user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            user32.FindWindowW.restype = wintypes.HWND
            user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                             wintypes.LPCWSTR, wintypes.LPCWSTR]
            user32.FindWindowExW.restype = wintypes.HWND
            user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                            wintypes.WPARAM, wintypes.LPARAM]
            user32.SendMessageW.restype = wintypes.LPARAM
            user32.SendMessageTimeoutW.argtypes = [
                wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                wintypes.UINT, wintypes.UINT, ctypes.POINTER(wintypes.DWORD_PTR)]
            user32.SendMessageTimeoutW.restype = wintypes.LPARAM
            user32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD

            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                             wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                                ctypes.c_size_t, wintypes.DWORD,
                                                wintypes.DWORD]
            kernel32.VirtualAllocEx.restype = ctypes.c_void_p
            kernel32.WriteProcessMemory.argtypes = [
                wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
            kernel32.WriteProcessMemory.restype = wintypes.BOOL
            kernel32.ReadProcessMemory.argtypes = [
                wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
            kernel32.ReadProcessMemory.restype = wintypes.BOOL
            kernel32.VirtualFreeEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                               ctypes.c_size_t, wintypes.DWORD]
            kernel32.VirtualFreeEx.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            shell32.SHGetKnownFolderPath.argtypes = [
                ctypes.POINTER(_GUID), wintypes.DWORD, wintypes.HANDLE,
                ctypes.POINTER(ctypes.c_void_p)]
            shell32.SHGetKnownFolderPath.restype = ctypes.c_long
            ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
            ole32.CoTaskMemFree.restype = None

            self._dlls = {"user32": user32, "kernel32": kernel32,
                          "shell32": shell32, "ole32": ole32}
            return self._dlls
        except Exception:
            self._dlls = None
            return None

    # ---------- 找 ListView 窗口 ----------
    def _find_listview(self):
        """找到桌面 SysListView32 的窗口句柄; 找不到返回 None。"""
        dlls = self._load()
        if dlls is None:
            return None
        user32 = dlls["user32"]
        try:
            progman = user32.FindWindowW("Progman", "Program Manager")
            if progman:
                # 未公开消息: 让资源管理器建出放桌面图标的 WorkerW
                user32.SendMessageW(progman, _WM_SPAWN_WORKER, 0, 0)
            worker = user32.FindWindowExW(None, None, "WorkerW", None)
            while worker:
                defview = user32.FindWindowExW(worker, None, "SHELLDLL_DefView", None)
                if defview:
                    lv = user32.FindWindowExW(defview, None, "SysListView32", None)
                    if lv:
                        return lv
                worker = user32.FindWindowExW(None, worker, "WorkerW", None)
            # 兜底: 有些版本里 SHELLDLL_DefView 直接挂在 Progman 下面
            if progman:
                defview = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
                if defview:
                    lv = user32.FindWindowExW(defview, None, "SysListView32", None)
                    if lv:
                        return lv
        except Exception:
            return None
        return None

    # ---------- 跨进程消息与远程内存 ----------
    def _send_timeout(self, hwnd, msg, wparam, lparam, timeout_ms=_SEND_TIMEOUT_MS):
        """SendMessageTimeoutW(SMTO_ABORTIFHUNG): 超时/失败返回 None, 不卡界面。"""
        try:
            result = wintypes.DWORD_PTR(0)
            ok = self._dlls["user32"].SendMessageTimeoutW(
                hwnd, msg, wparam, lparam, _SMTO_ABORTIFHUNG, timeout_ms,
                ctypes.byref(result))
            if not ok:
                return None
            return result.value
        except Exception:
            return None

    def _open_explorer(self, hwnd):
        """打开 ListView 所属进程(Explorer)的句柄; 失败返回 None。"""
        try:
            pid = wintypes.DWORD(0)
            self._dlls["user32"].GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value:
                return None
            hproc = self._dlls["kernel32"].OpenProcess(_PROCESS_VM_ACCESS,
                                                      False, pid.value)
            return hproc or None
        except Exception:
            return None

    def _remote_alloc(self, hproc, size):
        try:
            addr = self._dlls["kernel32"].VirtualAllocEx(
                hproc, None, size, _MEM_COMMIT_RESERVE, _PAGE_READWRITE)
            return addr or None
        except Exception:
            return None

    def _remote_free(self, hproc, addr):
        try:
            self._dlls["kernel32"].VirtualFreeEx(hproc, addr, 0, _MEM_RELEASE)
        except Exception:
            pass

    def _remote_write(self, hproc, addr, data):
        try:
            written = ctypes.c_size_t(0)
            ok = self._dlls["kernel32"].WriteProcessMemory(
                hproc, addr, data, len(data), ctypes.byref(written))
            return bool(ok) and written.value == len(data)
        except Exception:
            return False

    def _remote_read(self, hproc, addr, size):
        try:
            buf = ctypes.create_string_buffer(size)
            read = ctypes.c_size_t(0)
            ok = self._dlls["kernel32"].ReadProcessMemory(
                hproc, addr, buf, size, ctypes.byref(read))
            if not ok or read.value != size:
                return None
            return buf.raw
        except Exception:
            return None

    def _item_text(self, hproc, hwnd, index):
        """读第 index 个图标的显示名; 失败返回 None。"""
        lv_size = ctypes.sizeof(_LVITEMW)
        block = self._remote_alloc(hproc, lv_size + _TEXT_BUFFER_CHARS * 2)
        if not block:
            return None
        try:
            item = _LVITEMW()
            item.mask = LVIF_TEXT
            item.iItem = index
            item.iSubItem = 0
            item.pszText = block + lv_size
            item.cchTextMax = _TEXT_BUFFER_CHARS
            if not self._remote_write(hproc, block, bytes(item)):
                return None
            got = self._send_timeout(hwnd, LVM_GETITEMTEXTW, index, block)
            if not got:
                return None
            raw = self._remote_read(hproc, block + lv_size, _TEXT_BUFFER_CHARS * 2)
            if raw is None:
                return None
            text = raw.decode("utf-16-le", errors="ignore").split("\x00", 1)[0]
            return text or None
        finally:
            self._remote_free(hproc, block)

    def _item_pos(self, hproc, hwnd, index):
        """读第 index 个图标的位置 (x, y); 失败返回 None。"""
        addr = self._remote_alloc(hproc, ctypes.sizeof(_POINT))
        if not addr:
            return None
        try:
            got = self._send_timeout(hwnd, LVM_GETITEMPOSITION, index, addr)
            if not got:
                return None
            raw = self._remote_read(hproc, addr, ctypes.sizeof(_POINT))
            if raw is None:
                return None
            pt = _POINT.from_buffer_copy(raw)
            return (pt.x, pt.y)
        finally:
            self._remote_free(hproc, addr)

    # ---------- 对外接口 ----------
    def list_icons(self):
        """桌面全部图标: [{"name", "x", "y", "index"}]。

        非 Windows 或任何一步失败返回 []。单个图标读不到就跳过那个,
        不影响其他的。
        """
        if not self.enabled:
            return []
        try:
            dlls = self._load()
            if dlls is None:
                return []
            hwnd = self._find_listview()
            if not hwnd:
                return []
            count = self._send_timeout(hwnd, LVM_GETITEMCOUNT, 0, 0)
            if not count:
                return []
            hproc = self._open_explorer(hwnd)
            if not hproc:
                return []
            icons = []
            try:
                for index in range(count):
                    try:
                        name = self._item_text(hproc, hwnd, index)
                        if not name:
                            continue
                        pos = self._item_pos(hproc, hwnd, index)
                        if pos is None:
                            continue
                        icons.append({"name": name, "x": pos[0], "y": pos[1],
                                      "index": index})
                    except Exception:
                        continue
            finally:
                try:
                    dlls["kernel32"].CloseHandle(hproc)
                except Exception:
                    pass
            return icons
        except Exception:
            return []

    def get_position(self, name):
        """按显示名找图标位置 (x, y); 找不到返回 None。"""
        if not isinstance(name, str) or not name:
            return None
        try:
            for icon in self.list_icons():
                if icon["name"].lower() == name.lower():
                    return (icon["x"], icon["y"])
        except Exception:
            return None
        return None

    def set_position(self, name, x, y):
        """把图标挪到 (x, y); 成功返回 True。

        LVM_SETITEMPOSITION 的 lParam 直接打包坐标, 不用远程内存。
        非 Windows / 找不到图标 / 写失败返回 False。
        """
        if not self.enabled or not isinstance(name, str) or not name:
            return False
        try:
            x = int(x)
            y = int(y)
        except (TypeError, ValueError):
            return False
        try:
            dlls = self._load()
            if dlls is None:
                return False
            hwnd = self._find_listview()
            if not hwnd:
                return False
            target = None
            for icon in self.list_icons():
                if icon["name"].lower() == name.lower():
                    target = icon
                    break
            if target is None:
                return False
            packed = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
            got = self._send_timeout(hwnd, LVM_SETITEMPOSITION,
                                     target["index"], packed)
            return bool(got)
        except Exception:
            return False

    def desktop_dir(self):
        """桌面文件夹路径; 非 Windows 或取不到返回 None。

        优先 SHGetKnownFolderPath(FOLDERID_Desktop, 跟 OneDrive 搬家也对),
        取不到退回 %USERPROFILE%\\Desktop。
        """
        if self._desktop_dir_override is not None:
            return self._desktop_dir_override
        if not self.enabled:
            return None
        try:
            dlls = self._load()
            if dlls is not None:
                path = self._known_folder_desktop(dlls)
                if path and os.path.isdir(path):
                    return path
        except Exception:
            pass
        try:
            fallback = os.path.join(os.path.expanduser("~"), "Desktop")
            if os.path.isdir(fallback):
                return fallback
        except Exception:
            pass
        return None

    def _known_folder_desktop(self, dlls):
        try:
            ppsz = ctypes.c_void_p(0)
            hr = dlls["shell32"].SHGetKnownFolderPath(
                ctypes.byref(_FOLDERID_DESKTOP), 0, None, ctypes.byref(ppsz))
            if hr != 0 or not ppsz.value:
                return None
            try:
                return ctypes.wstring_at(ppsz.value)
            finally:
                try:
                    dlls["ole32"].CoTaskMemFree(ppsz)
                except Exception:
                    pass
        except Exception:
            return None

    def real_file_icons(self):
        """桌面文件夹里真实文件对应的图标(随机挑一个推的目标用)。

        显示名和文件名比对(去 .lnk、忽略大小写、藏扩展名也算);
        “此电脑”“回收站”等系统图标对不上文件, 自然跳过。
        非 Windows 或读不到返回 []。
        """
        if not self.enabled:
            return []
        try:
            icons = self.list_icons()
            folder = self.desktop_dir()
            if not folder:
                return []
            files = os.listdir(folder)
        except Exception:
            return []
        out = []
        for icon in icons:
            for fname in files:
                if display_matches_file(icon["name"], fname):
                    out.append(icon)
                    break
        return out
