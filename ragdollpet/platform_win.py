# -*- coding: utf-8 -*-
"""Windows 专属功能, 全部带平台守卫。

非 Windows 下所有函数都返回“不可用”的安全值, 不抛异常、不崩溃。
本模块只用标准库(ctypes / winreg), 不 import PyQt5。

功能:
    get_foreground_app()  前台应用(exe 名 + 窗口标题)
    set_autostart() / get_autostart()  开机自启(注册表 Run 键)
    register_hotkey() / unregister_hotkey()  全局快捷键(RegisterHotKey)
"""

import os
import sys

IS_WINDOWS = sys.platform == "win32"

WM_HOTKEY = 0x0312


def is_windows() -> bool:
    return IS_WINDOWS


# ================================ 前台应用 ================================
def get_foreground_app():
    """返回 {"exe": "Code.exe", "title": "窗口标题"}; 非 Windows 或失败返回 None。

    只读当前前台窗口, 不枚举、不注入、不写任何东西。
    """
    if not IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)

        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE,
                                               wintypes.LPWSTR, wintypes.DWORD]

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        title_buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title_buf, 512)
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        # PROCESS_QUERY_LIMITED_INFORMATION(0x1000) | PROCESS_VM_READ(0x10)
        handle = kernel32.OpenProcess(0x1010, False, pid.value)
        if not handle:
            return None
        try:
            path_buf = ctypes.create_unicode_buffer(1024)
            if not psapi.GetModuleFileNameExW(handle, None, path_buf, 1024):
                return None
            exe = os.path.basename(path_buf.value)
        finally:
            kernel32.CloseHandle(handle)
        if not exe:
            return None
        return {"exe": exe, "title": title_buf.value}
    except Exception:
        return None


# ================================ 开机自启 ================================
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _run_key_path():
    return _RUN_KEY


def get_autostart(name: str = "LiuYi") -> bool:
    """注册表 Run 键里有没有这条自启; 非 Windows 返回 False。"""
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, name)
        return True
    except OSError:
        return False
    except Exception:
        return False


def set_autostart(enabled: bool, name: str = "LiuYi", target: str = None) -> bool:
    """开关机自启。成功返回 True; 非 Windows 或写注册表失败返回 False。

    target 为空时: 打包版用 exe 本身; 源码版用 pythonw + 启动器路径。
    """
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enabled:
                if target is None:
                    if getattr(sys, "frozen", False):
                        target = f'"{sys.executable}"'
                    else:
                        launcher = os.path.join(
                            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "desktop_pet_v2.1.py")
                        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
                        target = f'"{pythonw}" "{launcher}"'
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, target)
            else:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


# ================================ 全局快捷键 ================================
def register_hotkey(hwnd: int, hotkey_id: int, mod_flags: int, vk: int) -> bool:
    """RegisterHotKey(hwnd, id, mods, vk)。非 Windows 返回 False。"""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        import ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                          ctypes.c_uint, ctypes.c_uint]
        user32.RegisterHotKey.restype = ctypes.c_int
        return bool(user32.RegisterHotKey(hwnd, hotkey_id, mod_flags, vk))
    except Exception:
        return False


def unregister_hotkey(hwnd: int, hotkey_id: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        import ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.UnregisterHotKey.restype = ctypes.c_int
        return bool(user32.UnregisterHotKey(hwnd, hotkey_id))
    except Exception:
        return False
