# -*- coding: utf-8 -*-
"""Windows 10 原生 Win32 任务栏嵌入式窗口、右键菜单与临时气球通知宿主。"""

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import time
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image

from core.models import DisplayMode, TaskbarPlacement, TrayMenuAction
from .renderer import image_to_premultiplied_bgra


# Win32 常量定义
WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_PAINT = 0x000F
WM_CLOSE = 0x0010
WM_ERASEBKGND = 0x0014
WM_MOUSEACTIVATE = 0x0021
WM_TIMER = 0x0113
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_MOUSELEAVE = 0x02A3
WM_SETTINGCHANGE = 0x001A
WM_DPICHANGED = 0x02E0
WM_DEVICECHANGE = 0x0219
WM_APP = 0x8000

DBT_DEVNODES_CHANGED = 0x0007
DBT_DEVICEARRIVAL = 0x8000
DBT_DEVICEREMOVECOMPLETE = 0x8004
DBT_DEVTYP_DEVICEINTERFACE = 0x00000005
DEVICE_NOTIFY_WINDOW_HANDLE = 0x00000000

WM_APP_REFRESH_UI = WM_APP + 1
WM_APP_NOTIFY = WM_APP + 2
WM_APP_QUIT = WM_APP + 3
WM_APP_BALLOON_CALLBACK = WM_APP + 4
WM_APP_TRAY_CALLBACK = WM_APP + 5

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4

TRAY_ICON_ID = 2001
BALLOON_ICON_ID = 1001

MA_NOACTIVATE = 3
CS_VREDRAW = 0x0001
CS_HREDRAW = 0x0002
CS_DBLCLKS = 0x0008

WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_CLIPSIBLINGS = 0x04000000

WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

GWL_STYLE = -16
GWL_EXSTYLE = -20
GW_HWNDNEXT = 2
GW_HWNDPREV = 3
GW_CHILD = 5
MONITOR_DEFAULTTOPRIMARY = 1
DWMWA_CLOAKED = 14

HWND_TOP = wintypes.HWND(0)
HWND_TOPMOST = wintypes.HWND(-1)
HWND_NOTOPMOST = wintypes.HWND(-2)
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SWP_HIDEWINDOW = 0x0080
SWP_ASYNCWINDOWPOS = 0x4000

ULW_ALPHA = 0x00000002
LWA_COLORKEY = 0x00000001
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
DIB_RGB_COLORS = 0
BI_RGB = 0
SRCCOPY = 0x00CC0020

TME_LEAVE = 0x00000002
IDC_ARROW = 32512
SM_CXSMICON = 49

MF_STRING = 0x00000000
MF_GRAYED = 0x00000001
MF_DISABLED = 0x00000002
MF_CHECKED = 0x00000008
MF_POPUP = 0x00000010
MF_SEPARATOR = 0x00000800
MIIM_FTYPE = 0x00000100
MIIM_STATE = 0x00000001
MFT_RADIOCHECK = 0x00000200
MFS_CHECKED = 0x00000008

TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100

TTF_IDISHWND = 0x0001
TTF_SUBCLASS = 0x0010
TTS_ALWAYSTIP = 0x01
TTS_NOPREFIX = 0x02
TTM_ACTIVATE = 0x0401
TTM_ADDTOOLW = 0x0432
TTM_UPDATETIPTEXTW = 0x0439
TTM_SETMAXTIPWIDTH = 0x0418

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
NIIF_WARNING = 0x00000002
NIN_BALLOONUSERCLICK = 0x0405
NIN_BALLOONTIMEOUT = 0x0404

TIMER_LAYOUT_ID = 1
TIMER_BALLOON_CLEANUP_ID = 2
TIMER_POST_ATTACH_REDRAW_ID = 3
TIMER_DEVICECHANGE_ID = 4
LAYOUT_POLL_MS = 250
SIBLING_RESCAN_INTERVAL_SEC = 2.5
POST_ATTACH_REDRAW_MS = 150
DEVICECHANGE_DEBOUNCE_MS = 250

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", wintypes.BYTE),
        ("BlendFlags", wintypes.BYTE),
        ("SourceConstantAlpha", wintypes.BYTE),
        ("AlphaFormat", wintypes.BYTE),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("hwndTrack", wintypes.HWND),
        ("dwHoverTime", wintypes.DWORD),
    ]


class TOOLINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("hwnd", wintypes.HWND),
        ("uId", ctypes.c_size_t),
        ("rect", RECT),
        ("hinst", wintypes.HINSTANCE),
        ("lpszText", wintypes.LPWSTR),
        ("lParam", wintypes.LPARAM),
        ("lpReserved", ctypes.c_void_p),
    ]


class MENUITEMINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("fMask", wintypes.UINT),
        ("fType", wintypes.UINT),
        ("fState", wintypes.UINT),
        ("wID", wintypes.UINT),
        ("hSubMenu", wintypes.HMENU),
        ("hbmpChecked", wintypes.HBITMAP),
        ("hbmpUnchecked", wintypes.HBITMAP),
        ("dwItemData", ctypes.c_size_t),
        ("dwTypeData", wintypes.LPWSTR),
        ("cch", wintypes.UINT),
        ("hbmpItem", wintypes.HBITMAP),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


class DEV_BROADCAST_DEVICEINTERFACE_W(ctypes.Structure):
    _fields_ = [
        ("dbcc_size", wintypes.DWORD),
        ("dbcc_devicetype", wintypes.DWORD),
        ("dbcc_reserved", wintypes.DWORD),
        ("dbcc_classguid", GUID),
        ("dbcc_name", wintypes.WCHAR * 1),
    ]


GUID_DEVINTERFACE_HID = GUID(
    0x4D1E55B2,
    0xF16F,
    0x11CF,
    (wintypes.BYTE * 8)(0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30),
)


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", wintypes.BYTE * 32),
    ]


@dataclass(frozen=True)
class RectTuple:
    """不可变矩形辅助结构，便于纯函数计算与单元测试。"""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


@dataclass(frozen=True)
class TaskbarLayoutResult:
    """Win10 任务栏 ReBar 子级布局计算结果（屏幕坐标）。"""

    bar_rect: RectTuple
    mstask_rect: Optional[RectTuple]
    need_resize_mstask: bool
    pushed_edge: Optional[int]
    is_horizontal: bool


def _place_avoiding_siblings(
    anchor_end: int,
    bar_size: int,
    min_safe_start: int,
    gap: int,
    siblings: List[RectTuple],
    placement: TaskbarPlacement,
    horizontal: bool,
) -> Tuple[int, int]:
    """根据停靠位置策略计算不与第三方任务栏组件重叠的一维区间 [start, end]。"""
    end = anchor_end
    start = end - bar_size
    if placement != TaskbarPlacement.LEFT_OF_SIBLINGS or not siblings:
        return start, end

    sorted_sibs = sorted(
        siblings,
        key=(lambda r: r.right) if horizontal else (lambda r: r.bottom),
        reverse=True,
    )
    for sib in sorted_sibs:
        sib_start = sib.left if horizontal else sib.top
        sib_end = sib.right if horizontal else sib.bottom
        if sib_end > start + 1 and sib_start < end - 1:
            candidate_end = sib_start - gap
            candidate_start = candidate_end - bar_size
            if candidate_start >= min_safe_start:
                end = candidate_end
                start = candidate_start
    return start, end


def _compute_classic_mstask_adjustment(
    anchor_start: int,
    anchor_end: int,
    mstask_start: int,
    mstask_end: int,
    bar_start: int,
    min_mstask_span: int,
    siblings: List[RectTuple],
    reclaim_gap: bool,
    horizontal: bool,
) -> Tuple[int, bool, Optional[int]]:
    """计算 Win10 MSTaskSwWClass 目标末端边界；若末端存在 TrafficMonitor 等兄弟组件，
    禁止缩窄或越界扩宽其占用的空间，从根本上消除退出/重启时的竞争闪烁。
    """
    half_line = anchor_start + (anchor_end - anchor_start) // 2
    has_tail_siblings = any(
        (s.right if horizontal else s.bottom) > half_line for s in siblings
    )
    if has_tail_siblings:
        return mstask_end, False, None

    target_end = max(mstask_start + min_mstask_span, bar_start)
    if mstask_start + min_mstask_span > bar_start:
        target_end = max(mstask_start, bar_start)

    overlaps = mstask_end > target_end + 1
    left_gap = reclaim_gap and mstask_end < target_end - 2
    if overlaps or left_gap:
        return target_end, True, bar_start
    return target_end, False, None


def compute_win10_layout(
    rc_tray: RectTuple,
    rc_notify: RectTuple,
    rc_rebar: Optional[RectTuple],
    bar_w: int,
    bar_h: int,
    scale: float = 1.0,
    rc_mstask: Optional[RectTuple] = None,
    sibling_widgets: Optional[List[RectTuple]] = None,
    last_pushed_edge: Optional[int] = None,
    reclaim_gap: bool = False,
    placement: TaskbarPlacement = TaskbarPlacement.LEFT_OF_SIBLINGS,
) -> TaskbarLayoutResult:
    """基于 ReBarWindow32 与 MSTaskSwWClass 计算嵌入式窗口与避让矩形。

    完全不改动 ReBarWindow32 自身尺寸，避免与 explorer.exe 托盘刷新产生拉锯抖动；
    同时按 placement 策略位于 TrafficMonitor 等第三方组件左侧（自动避让）或右侧。
    """
    is_horizontal = rc_tray.width >= rc_tray.height
    siblings = sibling_widgets or []
    gap = max(2, round(4 * scale))

    if rc_rebar is not None:
        anchor_rect = rc_rebar
    else:
        anchor_rect = RectTuple(rc_tray.left, rc_tray.top, rc_notify.left, rc_notify.bottom)

    if is_horizontal:
        min_mstask_w = max(48, round(80 * scale))
        min_safe_left = (rc_mstask.left if rc_mstask is not None else anchor_rect.left) + min_mstask_w
        if min_safe_left > anchor_rect.right - bar_w:
            min_safe_left = rc_mstask.left if rc_mstask is not None else anchor_rect.left

        bar_left, bar_right = _place_avoiding_siblings(
            anchor_end=anchor_rect.right,
            bar_size=bar_w,
            min_safe_start=min_safe_left,
            gap=gap,
            siblings=siblings,
            placement=placement,
            horizontal=True,
        )
        bar_top = anchor_rect.top + (anchor_rect.height - bar_h) // 2
        bar_rect = RectTuple(bar_left, bar_top, bar_right, bar_top + bar_h)

        if rc_mstask is None:
            return TaskbarLayoutResult(
                bar_rect=bar_rect,
                mstask_rect=None,
                need_resize_mstask=False,
                pushed_edge=None,
                is_horizontal=True,
            )

        target_right, need_resize, new_pushed_edge = _compute_classic_mstask_adjustment(
            anchor_start=anchor_rect.left,
            anchor_end=anchor_rect.right,
            mstask_start=rc_mstask.left,
            mstask_end=rc_mstask.right,
            bar_start=bar_left,
            min_mstask_span=min_mstask_w,
            siblings=siblings,
            reclaim_gap=reclaim_gap,
            horizontal=True,
        )
        new_mstask = RectTuple(rc_mstask.left, rc_mstask.top, target_right, rc_mstask.bottom)
        return TaskbarLayoutResult(
            bar_rect=bar_rect,
            mstask_rect=new_mstask,
            need_resize_mstask=need_resize,
            pushed_edge=new_pushed_edge,
            is_horizontal=True,
        )

    # 竖向任务栏（停靠左侧或右侧）
    actual_w = anchor_rect.width
    min_mstask_h = max(48, round(80 * scale))
    min_safe_top = (rc_mstask.top if rc_mstask is not None else anchor_rect.top) + min_mstask_h
    if min_safe_top > anchor_rect.bottom - bar_h:
        min_safe_top = rc_mstask.top if rc_mstask is not None else anchor_rect.top

    bar_top, bar_bottom = _place_avoiding_siblings(
        anchor_end=anchor_rect.bottom,
        bar_size=bar_h,
        min_safe_start=min_safe_top,
        gap=gap,
        siblings=siblings,
        placement=placement,
        horizontal=False,
    )
    bar_rect = RectTuple(anchor_rect.left, bar_top, anchor_rect.left + actual_w, bar_bottom)

    if rc_mstask is None:
        return TaskbarLayoutResult(
            bar_rect=bar_rect,
            mstask_rect=None,
            need_resize_mstask=False,
            pushed_edge=None,
            is_horizontal=False,
        )

    target_bottom, need_resize, new_pushed_edge = _compute_classic_mstask_adjustment(
        anchor_start=anchor_rect.top,
        anchor_end=anchor_rect.bottom,
        mstask_start=rc_mstask.top,
        mstask_end=rc_mstask.bottom,
        bar_start=bar_top,
        min_mstask_span=min_mstask_h,
        siblings=siblings,
        reclaim_gap=reclaim_gap,
        horizontal=False,
    )
    new_mstask = RectTuple(rc_mstask.left, rc_mstask.top, rc_mstask.right, target_bottom)
    return TaskbarLayoutResult(
        bar_rect=bar_rect,
        mstask_rect=new_mstask,
        need_resize_mstask=need_resize,
        pushed_edge=new_pushed_edge,
        is_horizontal=False,
    )


def compute_win11_layout(
    rc_tray: RectTuple,
    rc_notify: Optional[RectTuple],
    bar_w: int,
    bar_h: int,
    scale: float = 1.0,
    sibling_widgets: Optional[List[RectTuple]] = None,
    screen_rect: Optional[RectTuple] = None,
    placement: TaskbarPlacement = TaskbarPlacement.LEFT_OF_SIBLINGS,
) -> Optional[RectTuple]:
    """计算 Windows 11 XAML 任务栏悬浮窗停靠位置（屏幕坐标）。

    若任务栏处于自动隐藏收起状态则返回 None，供宿主隐藏悬浮条以免留在桌面上。
    """
    if rc_tray.width <= 0 or rc_tray.height <= 0:
        return None

    is_horizontal = rc_tray.width >= rc_tray.height
    if screen_rect is not None:
        if is_horizontal:
            if rc_tray.top >= screen_rect.bottom - 4 or rc_tray.bottom <= screen_rect.top + 4:
                return None
        elif rc_tray.right <= screen_rect.left + 4 or rc_tray.left >= screen_rect.right - 4:
            return None

    siblings = sibling_widgets or []
    gap = max(2, round(4 * scale))

    if is_horizontal:
        notify_valid = (
            rc_notify is not None
            and rc_notify.width > 0
            and rc_notify.height > 0
            and rc_notify.left > rc_tray.left + bar_w
            and rc_notify.right <= rc_tray.right + round(8 * scale)
            and rc_notify.width < rc_tray.width * 0.65
        )
        if notify_valid:
            anchor_right = rc_notify.left - gap
        else:
            fallback_notify_w = round(240 * scale)
            anchor_right = max(rc_tray.left + bar_w + gap, rc_tray.right - fallback_notify_w)

        base_right = min(rc_tray.right - gap, anchor_right)
        min_safe_left = rc_tray.left + round(rc_tray.width * 0.25)
        bar_left, bar_right = _place_avoiding_siblings(
            anchor_end=base_right,
            bar_size=bar_w,
            min_safe_start=min_safe_left,
            gap=gap,
            siblings=siblings,
            placement=placement,
            horizontal=True,
        )

        bar_top = rc_tray.top + (rc_tray.height - bar_h) // 2
        return RectTuple(bar_left, bar_top, bar_right, bar_top + bar_h)

    notify_valid = (
        rc_notify is not None
        and rc_notify.height > 0
        and rc_notify.top > rc_tray.top + bar_h
        and rc_notify.bottom <= rc_tray.bottom + round(8 * scale)
        and rc_notify.height < rc_tray.height * 0.65
    )
    if notify_valid:
        anchor_bottom = rc_notify.top - gap
    else:
        anchor_bottom = max(rc_tray.top + bar_h + gap, rc_tray.bottom - round(160 * scale))

    base_bottom = min(rc_tray.bottom - gap, anchor_bottom)
    min_safe_top = rc_tray.top + round(rc_tray.height * 0.25)
    bar_top, bar_bottom = _place_avoiding_siblings(
        anchor_end=base_bottom,
        bar_size=bar_h,
        min_safe_start=min_safe_top,
        gap=gap,
        siblings=siblings,
        placement=placement,
        horizontal=False,
    )
    bar_left = rc_tray.left + max(0, (rc_tray.width - bar_w) // 2)
    return RectTuple(bar_left, bar_top, bar_left + bar_w, bar_bottom)


def _init_win32_apis():
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    g32 = ctypes.WinDLL("gdi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    s32 = ctypes.WinDLL("shell32", use_last_error=True)

    k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    k32.GetModuleHandleW.restype = wintypes.HMODULE

    u32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    u32.RegisterClassExW.restype = wintypes.ATOM
    u32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    u32.UnregisterClassW.restype = wintypes.BOOL

    u32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    u32.CreateWindowExW.restype = wintypes.HWND
    u32.DestroyWindow.argtypes = [wintypes.HWND]
    u32.DestroyWindow.restype = wintypes.BOOL
    u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u32.ShowWindow.restype = wintypes.BOOL
    u32.IsWindow.argtypes = [wintypes.HWND]
    u32.IsWindow.restype = wintypes.BOOL

    u32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    u32.GetWindow.restype = wintypes.HWND
    u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u32.GetClassNameW.restype = ctypes.c_int
    u32.IsWindowVisible.argtypes = [wintypes.HWND]
    u32.IsWindowVisible.restype = wintypes.BOOL
    u32.IsIconic.argtypes = [wintypes.HWND]
    u32.IsIconic.restype = wintypes.BOOL
    u32.GetParent.argtypes = [wintypes.HWND]
    u32.GetParent.restype = wintypes.HWND
    u32.GetForegroundWindow.argtypes = []
    u32.GetForegroundWindow.restype = wintypes.HWND
    u32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    u32.EnumWindows.restype = wintypes.BOOL
    u32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u32.MonitorFromWindow.restype = wintypes.HMONITOR
    u32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    u32.GetMonitorInfoW.restype = wintypes.BOOL
    u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    u32.FindWindowW.restype = wintypes.HWND
    u32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    u32.FindWindowExW.restype = wintypes.HWND
    u32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
    u32.SetParent.restype = wintypes.HWND

    u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    u32.GetWindowRect.restype = wintypes.BOOL
    u32.MapWindowPoints.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_void_p, wintypes.UINT]
    u32.MapWindowPoints.restype = ctypes.c_int
    u32.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL]
    u32.MoveWindow.restype = wintypes.BOOL
    u32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT
    ]
    u32.SetWindowPos.restype = wintypes.BOOL

    u32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    u32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t

    u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u32.DefWindowProcW.restype = LRESULT
    u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u32.SendMessageW.restype = LRESULT
    u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u32.PostMessageW.restype = wintypes.BOOL
    u32.PostQuitMessage.argtypes = [ctypes.c_int]
    u32.PostQuitMessage.restype = None
    u32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    u32.RegisterWindowMessageW.restype = wintypes.UINT
    u32.RegisterDeviceNotificationW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    u32.RegisterDeviceNotificationW.restype = wintypes.HANDLE
    u32.UnregisterDeviceNotification.argtypes = [wintypes.HANDLE]
    u32.UnregisterDeviceNotification.restype = wintypes.BOOL

    u32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    u32.GetMessageW.restype = wintypes.BOOL
    u32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    u32.TranslateMessage.restype = wintypes.BOOL
    u32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    u32.DispatchMessageW.restype = LRESULT

    u32.GetDC.argtypes = [wintypes.HWND]
    u32.GetDC.restype = wintypes.HDC
    u32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    u32.ReleaseDC.restype = ctypes.c_int
    u32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
    u32.BeginPaint.restype = wintypes.HDC
    u32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
    u32.EndPaint.restype = wintypes.BOOL
    u32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT), wintypes.BOOL]
    u32.InvalidateRect.restype = wintypes.BOOL

    u32.UpdateLayeredWindow.argtypes = [
        wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE),
        wintypes.HDC, ctypes.POINTER(POINT), wintypes.COLORREF,
        ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD,
    ]
    u32.UpdateLayeredWindow.restype = wintypes.BOOL
    u32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD]
    u32.SetLayeredWindowAttributes.restype = wintypes.BOOL

    u32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    u32.LoadCursorW.restype = wintypes.HANDLE
    u32.TrackMouseEvent.argtypes = [ctypes.POINTER(TRACKMOUSEEVENT)]
    u32.TrackMouseEvent.restype = wintypes.BOOL
    u32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
    u32.SetTimer.restype = ctypes.c_size_t
    u32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
    u32.KillTimer.restype = wintypes.BOOL

    u32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    u32.GetCursorPos.restype = wintypes.BOOL
    u32.SetForegroundWindow.argtypes = [wintypes.HWND]
    u32.SetForegroundWindow.restype = wintypes.BOOL

    u32.CreatePopupMenu.argtypes = []
    u32.CreatePopupMenu.restype = wintypes.HMENU
    u32.DestroyMenu.argtypes = [wintypes.HMENU]
    u32.DestroyMenu.restype = wintypes.BOOL
    u32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    u32.AppendMenuW.restype = wintypes.BOOL
    u32.SetMenuItemInfoW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.BOOL, ctypes.POINTER(MENUITEMINFOW)]
    u32.SetMenuItemInfoW.restype = wintypes.BOOL
    u32.TrackPopupMenuEx.argtypes = [
        wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p
    ]
    u32.TrackPopupMenuEx.restype = wintypes.BOOL

    u32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
    u32.CreateIconIndirect.restype = wintypes.HICON
    u32.DestroyIcon.argtypes = [wintypes.HICON]
    u32.DestroyIcon.restype = wintypes.BOOL
    u32.GetSystemMetrics.argtypes = [ctypes.c_int]
    u32.GetSystemMetrics.restype = ctypes.c_int
    if hasattr(u32, "GetSystemMetricsForDpi"):
        u32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]
        u32.GetSystemMetricsForDpi.restype = ctypes.c_int

    g32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    g32.CreateCompatibleDC.restype = wintypes.HDC
    g32.DeleteDC.argtypes = [wintypes.HDC]
    g32.DeleteDC.restype = wintypes.BOOL
    g32.CreateDIBSection.argtypes = [
        wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
    ]
    g32.CreateDIBSection.restype = wintypes.HBITMAP
    g32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
    g32.CreateBitmap.restype = wintypes.HBITMAP
    g32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    g32.SelectObject.restype = wintypes.HGDIOBJ
    g32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g32.DeleteObject.restype = wintypes.BOOL
    g32.BitBlt.argtypes = [
        wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
    ]
    g32.BitBlt.restype = wintypes.BOOL
    g32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
    g32.GetDeviceCaps.restype = ctypes.c_int

    s32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    s32.Shell_NotifyIconW.restype = wintypes.BOOL

    return u32, g32, k32, s32


def enable_dpi_awareness() -> None:
    """启用 Per-Monitor V2 DPI 感知，保证任务栏坐标与字体缩放精确。"""
    try:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        if hasattr(u32, "SetProcessDpiAwarenessContext"):
            u32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_ssize_t]
            u32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
            if u32.SetProcessDpiAwarenessContext(-4):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
                return
        if hasattr(u32, "SetProcessDPIAware"):
            u32.SetProcessDPIAware()
    except Exception:
        pass


def create_hbitmap_from_rgba(g32, screen_dc: wintypes.HDC, img: Image.Image) -> Tuple[wintypes.HBITMAP, int, int]:
    """将 RGBA Image 写入 Top-Down 32bpp 预乘 BGRA DIBSECTION。"""
    w, h = img.size
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h  # 负值表示自顶向下坐标系
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB

    bits_ptr = ctypes.c_void_p()
    hbmp = g32.CreateDIBSection(screen_dc, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits_ptr), None, 0)
    if not hbmp or not bits_ptr.value:
        raise RuntimeError("CreateDIBSection 失败")

    bgra = image_to_premultiplied_bgra(img)
    ctypes.memmove(bits_ptr.value, bgra, len(bgra))
    return hbmp, w, h


def create_hicon_from_image(u32, g32, img: Image.Image) -> Optional[wintypes.HICON]:
    """根据 Pillow RGBA Image 创建 Win32 HICON（用于临时系统气球通知图标）。"""
    screen_dc = u32.GetDC(None)
    hbmp_color = None
    hbmp_mask = None
    try:
        hbmp_color, w, h = create_hbitmap_from_rgba(g32, screen_dc, img)
        hbmp_mask = g32.CreateBitmap(w, h, 1, 1, None)
        info = ICONINFO()
        info.fIcon = True
        info.xHotspot = 0
        info.yHotspot = 0
        info.hbmMask = hbmp_mask
        info.hbmColor = hbmp_color
        return u32.CreateIconIndirect(ctypes.byref(info))
    except Exception:
        return None
    finally:
        if hbmp_color:
            g32.DeleteObject(hbmp_color)
        if hbmp_mask:
            g32.DeleteObject(hbmp_mask)
        if screen_dc:
            u32.ReleaseDC(None, screen_dc)


class Win32TaskbarHost:
    """管理 Windows 10 嵌入式与 Windows 11 悬浮锚定任务栏长条、系统托盘及低电量气泡宿主。"""

    BAR_CLASS_NAME = "MchoseG3V2TaskbarWidgetClass"
    MSG_CLASS_NAME = "MchoseG3V2TrayMsgClass"
    CLASS_NAME = BAR_CLASS_NAME

    _WIN11_IGNORED_SIBLING_CLASSES = {
        "Shell_TrayWnd",
        "Shell_SecondaryTrayWnd",
        "Progman",
        "WorkerW",
        "tooltips_class32",
        "#32768",
        "NotifyIconOverflowWindow",
        "TopLevelWindowForOverflowXamlIsland",
        "Windows.UI.Core.CoreWindow",
        "Windows.UI.Input.InputSite.WindowClass",
        "Windows.UI.Composition.DesktopWindowContentBridge",
        "Xaml_WindowedPopupClass",
        "ApplicationFrameWindow",
        "ReBarWindow32",
        "TrayNotifyWnd",
        "Start",
        "TrayButton",
        "TrayDummySearchControl",
        "MSTaskSwWClass",
        "MSTaskListWClass",
        "ToolbarWindow32",
        "TrayClockWClass",
        "TrayShowDesktopButtonWClass",
        BAR_CLASS_NAME,
        MSG_CLASS_NAME,
    }

    _FULLSCREEN_IGNORED_CLASSES = {
        "Shell_TrayWnd",
        "Shell_SecondaryTrayWnd",
        "Progman",
        "WorkerW",
        "Windows.UI.Core.CoreWindow",
        "StartMenuExperienceHost",
        "SearchHost",
        "SearchUI",
        "MultitaskingViewFrame",
        "TaskListThumbnailWnd",
        "ForegroundStaging",
        "Xaml_WindowedPopupClass",
        "XamlExplorerHostIslandWindow",
        "NotifyIconOverflowWindow",
        "TopLevelWindowForOverflowXamlIsland",
        "#32768",
    }

    def __init__(
        self,
        on_double_click: Callable[[], None],
        build_menu_items: Callable[[], List[TrayMenuAction]],
        render_widget_fn: Callable[[int, int, bool, Optional[Tuple[int, int, int]]], Image.Image],
        measure_widget_fn: Callable[[int, int], Tuple[int, int]],
        get_tooltip_text: Callable[[], str],
        get_balloon_icon_img: Callable[[], Image.Image],
        is_light_theme_fn: Callable[[], bool],
        get_tray_icon_img: Optional[Callable[[int], Image.Image]] = None,
        initial_mode: DisplayMode = DisplayMode.TASKBAR_BAR,
        initial_placement: TaskbarPlacement = TaskbarPlacement.LEFT_OF_SIBLINGS,
        on_device_change: Optional[Callable[[], None]] = None,
    ) -> None:
        self._on_double_click = on_double_click
        self._on_device_change = on_device_change
        self._build_menu_items = build_menu_items
        self._render_widget_fn = render_widget_fn
        self._measure_widget_fn = measure_widget_fn
        self._get_tooltip_text = get_tooltip_text
        self._get_balloon_icon_img = get_balloon_icon_img
        self._get_tray_icon_img = get_tray_icon_img or (lambda _sz: get_balloon_icon_img())
        self._is_light_theme_fn = is_light_theme_fn
        self._display_mode = initial_mode
        self._taskbar_placement = initial_placement

        self._u32, self._g32, self._k32, self._s32 = _init_win32_apis()
        self._hinst = self._k32.GetModuleHandleW(None)
        self._wm_taskbar_created = self._u32.RegisterWindowMessageW("TaskbarCreated")

        # 永久隐藏消息/托盘窗口与可重建任务栏常驻条窗口彻底分离，避免 Reparent 污染 DWM 图层
        self._msg_hwnd: Optional[wintypes.HWND] = None
        self._bar_hwnd: Optional[wintypes.HWND] = None
        self._tooltip_hwnd: Optional[wintypes.HWND] = None
        self._tooltip_cb_size: int = ctypes.sizeof(TOOLINFOW)

        self._h_tray: Optional[wintypes.HWND] = None
        self._h_rebar: Optional[wintypes.HWND] = None
        self._h_mstask: Optional[wintypes.HWND] = None
        self._h_notify: Optional[wintypes.HWND] = None
        self._parent_hwnd: Optional[wintypes.HWND] = None
        self._embedded: bool = False
        self._win11_overlay: bool = False
        self._bar_hidden_by_cover: bool = False
        self._last_pushed_edge: Optional[int] = None
        self._last_anchor_edge: Optional[int] = None
        self._last_bar_left: Optional[int] = None
        self._modified_mstask: bool = False
        self._cached_sibling_hwnds: List[int] = []
        self._last_nonempty_siblings: List[RectTuple] = []
        self._empty_sibling_rescan_streak: int = 0
        self._last_sibling_scan_ts: float = 0.0
        self._empty_classic_sibling_polls: int = 0

        self._hovered: bool = False
        self._use_colorkey_fallback: bool = False
        self._last_colorkey_light: Optional[bool] = None
        self._last_theme_light: Optional[bool] = None
        self._dwm_attr_initialized: bool = False
        self._dwm_attr_fn = None
        self._cached_fallback_bmp: Optional[wintypes.HBITMAP] = None
        self._cached_size: Tuple[int, int] = (108, 40)
        self._current_dpi: int = 96

        self._tray_active: bool = False
        self._tray_hicon: Optional[wintypes.HICON] = None
        self._balloon_active: bool = False
        self._balloon_hicon: Optional[wintypes.HICON] = None
        self._pending_alerts: List[Tuple[str, str]] = []
        self._dev_notify_handle: Optional[wintypes.HANDLE] = None

        self._msg_wndproc_ref = WNDPROC(self._msg_wnd_proc)
        self._bar_wndproc_ref = WNDPROC(self._bar_wnd_proc)

    def create(self) -> None:
        """创建常驻消息宿主与初始界面。"""
        enable_dpi_awareness()
        self._register_classes()

        self._msg_hwnd = self._u32.CreateWindowExW(
            WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            self.MSG_CLASS_NAME,
            "MchoseBatteryMsgHost",
            WS_POPUP,
            0,
            0,
            0,
            0,
            None,
            None,
            self._hinst,
            None,
        )
        if not self._msg_hwnd:
            raise RuntimeError("创建消息宿主窗口失败")

        self._register_device_notifications()

        if self._display_mode == DisplayMode.TASKBAR_BAR:
            self._ensure_bar_and_sync(schedule_post_redraw=True)
        else:
            self._add_or_update_tray_icon()

    def set_display_mode(self, mode: DisplayMode) -> None:
        """在任务栏常驻条与仅托盘图标之间实时无缝切换。"""
        if self._display_mode == mode:
            return
        self._display_mode = mode
        if not self._msg_hwnd:
            return

        if mode == DisplayMode.TRAY_ICON_ONLY:
            self._stop_bar_timers()
            self._cleanup_balloon_icon()
            self._destroy_bar_window()
            self._add_or_update_tray_icon()
        else:
            self._remove_tray_icon()
            self._ensure_bar_and_sync(schedule_post_redraw=True)

    def set_taskbar_placement(self, placement: TaskbarPlacement) -> None:
        """实时切换相对 TrafficMonitor 等组件的停靠位置并立即重排。"""
        if self._taskbar_placement == placement:
            return
        self._taskbar_placement = placement
        self._last_pushed_edge = None
        if not self._msg_hwnd:
            return
        if self._display_mode == DisplayMode.TASKBAR_BAR:
            self.force_relayout_and_redraw()

    def force_relayout_and_redraw(self) -> None:
        """在显示样式切换时立即重算宽度、同步布局并重绘。"""
        if not self._msg_hwnd:
            return
        if self._display_mode == DisplayMode.TASKBAR_BAR:
            if not self._bar_hwnd or not self._u32.IsWindow(self._bar_hwnd):
                self._ensure_bar_and_sync(schedule_post_redraw=True)
                return
            self._sync_layout(force=True)
            self.redraw()
            self.update_tooltip()
        else:
            self._add_or_update_tray_icon()

    def _register_classes(self) -> None:
        for class_name, wndproc_ref, style in (
            (self.MSG_CLASS_NAME, self._msg_wndproc_ref, 0),
            (self.BAR_CLASS_NAME, self._bar_wndproc_ref, CS_DBLCLKS | CS_HREDRAW | CS_VREDRAW),
        ):
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.style = style
            wc.lpfnWndProc = wndproc_ref
            wc.hInstance = self._hinst
            wc.hCursor = self._u32.LoadCursorW(None, ctypes.c_void_p(IDC_ARROW))
            wc.lpszClassName = class_name
            if not self._u32.RegisterClassExW(ctypes.byref(wc)):
                err = ctypes.get_last_error()
                if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                    raise RuntimeError(f"RegisterClassExW 失败 ({class_name}): {err}")

    def _register_device_notifications(self) -> None:
        if not self._msg_hwnd or self._dev_notify_handle:
            return
        try:
            flt = DEV_BROADCAST_DEVICEINTERFACE_W()
            flt.dbcc_size = ctypes.sizeof(DEV_BROADCAST_DEVICEINTERFACE_W)
            flt.dbcc_devicetype = DBT_DEVTYP_DEVICEINTERFACE
            flt.dbcc_reserved = 0
            flt.dbcc_classguid = GUID_DEVINTERFACE_HID
            handle = self._u32.RegisterDeviceNotificationW(
                self._msg_hwnd,
                ctypes.byref(flt),
                DEVICE_NOTIFY_WINDOW_HANDLE,
            )
            if handle:
                self._dev_notify_handle = handle
        except Exception:
            self._dev_notify_handle = None

    def _unregister_device_notifications(self) -> None:
        if self._msg_hwnd:
            try:
                self._u32.KillTimer(self._msg_hwnd, TIMER_DEVICECHANGE_ID)
            except Exception:
                pass
        if self._dev_notify_handle:
            try:
                self._u32.UnregisterDeviceNotification(self._dev_notify_handle)
            except Exception:
                pass
            self._dev_notify_handle = None

    def _stop_bar_timers(self) -> None:
        if not self._msg_hwnd:
            return
        self._u32.KillTimer(self._msg_hwnd, TIMER_LAYOUT_ID)
        self._u32.KillTimer(self._msg_hwnd, TIMER_POST_ATTACH_REDRAW_ID)

    def _should_use_overlay_on_create(self) -> bool:
        """创建常驻条窗口前预判是否需要原生 WS_EX_TOPMOST 悬浮层（仅当找不到任务栏时兜底启用）。"""
        self._detect_taskbar_handles()
        return not bool(self._h_tray)

    def _ensure_bar_and_sync(self, schedule_post_redraw: bool) -> None:
        self._destroy_bar_window()
        use_overlay = self._should_use_overlay_on_create()
        if not self._create_bar_window(topmost=use_overlay):
            return
        self._attach_to_taskbar()
        self._create_tooltip()
        self._sync_layout(force=True)
        self.redraw()
        self.update_tooltip()
        if self._msg_hwnd:
            self._u32.SetTimer(self._msg_hwnd, TIMER_LAYOUT_ID, LAYOUT_POLL_MS, None)
            if schedule_post_redraw:
                self._u32.SetTimer(self._msg_hwnd, TIMER_POST_ATTACH_REDRAW_ID, POST_ATTACH_REDRAW_MS, None)

    def _create_bar_window(self, topmost: bool = False) -> bool:
        ex_style = WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        if topmost:
            ex_style |= WS_EX_TOPMOST
        hwnd = self._u32.CreateWindowExW(
            ex_style,
            self.BAR_CLASS_NAME,
            "MchoseBatteryBar",
            WS_POPUP,
            0,
            0,
            108,
            40,
            None,
            None,
            self._hinst,
            None,
        )
        if not hwnd:
            return False
        self._bar_hwnd = hwnd
        self._hovered = False
        self._use_colorkey_fallback = False
        self._last_colorkey_light = None
        self._bar_hidden_by_cover = False
        return True

    def _destroy_bar_window(self) -> None:
        self._destroy_tooltip()
        if self._embedded or self._modified_mstask:
            self._restore_taskbar_layout()
        if self._bar_hwnd:
            hwnd = self._bar_hwnd
            self._bar_hwnd = None
            if self._u32.IsWindow(hwnd):
                self._u32.DestroyWindow(hwnd)
        if self._cached_fallback_bmp:
            self._g32.DeleteObject(self._cached_fallback_bmp)
            self._cached_fallback_bmp = None
        self._parent_hwnd = None
        self._embedded = False
        self._win11_overlay = False
        self._bar_hidden_by_cover = False
        self._last_pushed_edge = None
        self._last_anchor_edge = None
        self._last_bar_left = None
        self._modified_mstask = False
        self._cached_sibling_hwnds = []
        self._last_nonempty_siblings = []
        self._empty_sibling_rescan_streak = 0
        self._last_sibling_scan_ts = 0.0
        self._empty_classic_sibling_polls = 0
        self._hovered = False
        self._use_colorkey_fallback = False
        self._last_colorkey_light = None

    def _create_tooltip(self) -> None:
        if not self._bar_hwnd:
            return
        tip_hwnd = self._u32.CreateWindowExW(
            WS_EX_TOPMOST,
            "tooltips_class32",
            None,
            WS_POPUP | TTS_ALWAYSTIP | TTS_NOPREFIX,
            0,
            0,
            0,
            0,
            self._bar_hwnd,
            None,
            self._hinst,
            None,
        )
        if not tip_hwnd:
            return
        self._u32.SetWindowPos(tip_hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        self._u32.SendMessageW(tip_hwnd, TTM_SETMAXTIPWIDTH, 0, 400)
        self._u32.SendMessageW(tip_hwnd, TTM_ACTIVATE, 1, 0)

        ti = TOOLINFOW()
        ti.cbSize = ctypes.sizeof(TOOLINFOW)
        ti.uFlags = TTF_IDISHWND | TTF_SUBCLASS
        ti.hwnd = self._bar_hwnd
        ti.uId = int(self._bar_hwnd)
        ti.lpszText = self._get_tooltip_text()

        ok = self._u32.SendMessageW(tip_hwnd, TTM_ADDTOOLW, 0, ctypes.addressof(ti))
        if not ok:
            ti.cbSize = ctypes.sizeof(TOOLINFOW) - ctypes.sizeof(ctypes.c_void_p)
            ok = self._u32.SendMessageW(tip_hwnd, TTM_ADDTOOLW, 0, ctypes.addressof(ti))
        if ok:
            self._tooltip_cb_size = ti.cbSize
        self._tooltip_hwnd = tip_hwnd

    def _destroy_tooltip(self) -> None:
        if self._tooltip_hwnd:
            tip = self._tooltip_hwnd
            self._tooltip_hwnd = None
            if self._u32.IsWindow(tip):
                self._u32.DestroyWindow(tip)

    def update_tooltip(self) -> None:
        if not self._tooltip_hwnd or not self._bar_hwnd:
            return
        ti = TOOLINFOW()
        ti.cbSize = self._tooltip_cb_size
        ti.uFlags = TTF_IDISHWND | TTF_SUBCLASS
        ti.hwnd = self._bar_hwnd
        ti.uId = int(self._bar_hwnd)
        ti.lpszText = self._get_tooltip_text()
        self._u32.SendMessageW(self._tooltip_hwnd, TTM_UPDATETIPTEXTW, 0, ctypes.addressof(ti))

    def _get_window_rect(self, hwnd: Optional[wintypes.HWND]) -> Optional[RectTuple]:
        rc = RECT()
        if not hwnd or not self._u32.GetWindowRect(hwnd, ctypes.byref(rc)):
            return None
        return RectTuple(rc.left, rc.top, rc.right, rc.bottom)

    def _get_window_class(self, hwnd: Optional[wintypes.HWND]) -> str:
        if not hwnd:
            return ""
        buf = ctypes.create_unicode_buffer(128)
        self._u32.GetClassNameW(hwnd, buf, 128)
        return buf.value

    def _get_monitor_rect(self, hwnd: Optional[wintypes.HWND]) -> Optional[RectTuple]:
        if not hwnd:
            return None
        hmon = self._u32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTOPRIMARY)
        if not hmon:
            return None
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not self._u32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return None
        rc = mi.rcMonitor
        return RectTuple(rc.left, rc.top, rc.right, rc.bottom)

    def _get_dpi_for_hwnd(self, hwnd: Optional[wintypes.HWND]) -> int:
        try:
            if hwnd and hasattr(self._u32, "GetDpiForWindow"):
                self._u32.GetDpiForWindow.argtypes = [wintypes.HWND]
                self._u32.GetDpiForWindow.restype = wintypes.UINT
                dpi = int(self._u32.GetDpiForWindow(hwnd))
                if dpi > 0:
                    return dpi
        except Exception:
            pass
        dc = self._u32.GetDC(None)
        try:
            return max(96, int(self._g32.GetDeviceCaps(dc, 88)))  # LOGPIXELSX
        finally:
            if dc:
                self._u32.ReleaseDC(None, dc)

    def _has_visible_win11_xaml_bridge(self, h_tray: wintypes.HWND) -> bool:
        """检测 Shell_TrayWnd 是否正在由 Win11 XAML DesktopWindowContentBridge 承载。"""
        bridge = self._u32.FindWindowExW(
            h_tray, None, "Windows.UI.Composition.DesktopWindowContentBridge", None
        )
        steps = 0
        while bridge and steps < 32:
            if self._u32.IsWindowVisible(bridge):
                return True
            bridge = self._u32.FindWindowExW(
                h_tray, bridge, "Windows.UI.Composition.DesktopWindowContentBridge", None
            )
            steps += 1
        return False

    def _detect_taskbar_handles(self) -> bool:
        """定位任务栏句柄并识别当前为 Win10 经典 ReBar 还是 Win11 现代 XAML 任务栏。"""
        h_tray = self._u32.FindWindowW("Shell_TrayWnd", None)
        if not h_tray:
            self._h_tray = None
            self._h_rebar = None
            self._h_mstask = None
            self._h_notify = None
            return False

        h_rebar = self._u32.FindWindowExW(h_tray, None, "ReBarWindow32", None)
        h_mstask = self._u32.FindWindowExW(h_rebar, None, "MSTaskSwWClass", None) if h_rebar else None
        h_notify = self._u32.FindWindowExW(h_tray, None, "TrayNotifyWnd", None)

        self._h_tray = h_tray
        self._h_rebar = h_rebar or None
        self._h_mstask = h_mstask or None
        self._h_notify = h_notify or None

        rc_tray = self._get_window_rect(h_tray)
        rc_rebar = self._get_window_rect(self._h_rebar)
        rc_mstask = self._get_window_rect(self._h_mstask)
        rc_notify = self._get_window_rect(self._h_notify)
        is_classic = bool(
            self._h_rebar
            and self._h_mstask
            and self._u32.IsWindowVisible(self._h_rebar)
            and self._u32.IsWindowVisible(self._h_mstask)
            and not self._has_visible_win11_xaml_bridge(h_tray)
            and rc_rebar is not None
            and rc_mstask is not None
            and rc_rebar.width > 0
            and rc_rebar.height > 0
            and rc_mstask.width > 0
            and rc_mstask.height > 0
        )
        # StartAllBack 居中任务栏等场景下，ReBarWindow32 仅包裹中间图标区而与托盘区之间存在大段留白，
        # 此时应改走 Shell_TrayWnd 挂载，使组件紧贴 TrayNotifyWnd / TrafficMonitor 停靠
        if (
            is_classic
            and rc_tray is not None
            and rc_notify is not None
            and rc_notify.width > 0
            and rc_notify.height > 0
        ):
            scale = self._get_dpi_for_hwnd(h_tray) / 96.0
            max_gap = max(48, round(48 * scale))
            is_horizontal = rc_tray.width >= rc_tray.height
            gap_to_notify = (
                (rc_notify.left - rc_rebar.right)
                if is_horizontal
                else (rc_notify.top - rc_rebar.bottom)
            )
            if gap_to_notify > max_gap:
                is_classic = False
        return is_classic

    def _embed_bar_in_parent(self, parent_hwnd: wintypes.HWND) -> bool:
        """将常驻条挂载到任务栏容器（Win10 ReBarWindow32 或 Win11 Shell_TrayWnd）。

        保持 WS_POPUP | WS_CLIPSIBLINGS 以支持跨进程 UpdateLayeredWindow(ULW_ALPHA)，
        同时随 Shell_TrayWnd 处于同一系统 Z 序层级，避免弹出开始菜单或关闭窗口时被任务栏遮盖。
        """
        if not self._bar_hwnd or not parent_hwnd:
            return False
        if self._u32.GetWindowLongPtrW(self._bar_hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST:
            self._u32.SetWindowPos(
                self._bar_hwnd,
                HWND_NOTOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        style = self._u32.GetWindowLongPtrW(self._bar_hwnd, GWL_STYLE)
        style = (style & ~WS_CHILD) | WS_POPUP | WS_CLIPSIBLINGS
        self._u32.SetWindowLongPtrW(self._bar_hwnd, GWL_STYLE, style)
        # 对首次挂载的顶层窗口，SetParent 成功时返回旧父窗口 NULL(None)，需结合 LastError 判定
        ctypes.set_last_error(0)
        prev_parent = self._u32.SetParent(self._bar_hwnd, parent_hwnd)
        err = ctypes.get_last_error()
        if prev_parent or err == 0:
            self._parent_hwnd = parent_hwnd
            self._embedded = True
            # 跨进程 SetParent 到 explorer 任务栏容器时，直接走 LWA_COLORKEY + WM_PAINT 以确保 DWM 正常合成可见
            self._use_colorkey_fallback = True
            return True
        return False

    def _attach_to_taskbar(self) -> None:
        if not self._bar_hwnd:
            return
        is_classic = self._detect_taskbar_handles()
        if not self._h_tray:
            return

        self._last_pushed_edge = None
        self._last_anchor_edge = None
        self._last_bar_left = None
        self._modified_mstask = False
        self._empty_classic_sibling_polls = 0

        # Win10 经典任务栏挂载为 ReBarWindow32 子级；Win11 现代 XAML 任务栏挂载为 Shell_TrayWnd 子级（与 TrafficMonitor 同构）
        if is_classic and self._h_rebar:
            if self._embed_bar_in_parent(self._h_rebar):
                self._win11_overlay = False
                return
        elif not is_classic and self._h_tray:
            if self._embed_bar_in_parent(self._h_tray):
                self._win11_overlay = True
                return

        # 无法挂载到任务栏容器时，回退为独立置顶悬浮层
        if self._parent_hwnd or self._u32.GetParent(self._bar_hwnd):
            self._u32.SetParent(self._bar_hwnd, None)
        self._parent_hwnd = None
        self._embedded = False
        self._win11_overlay = True
        style = self._u32.GetWindowLongPtrW(self._bar_hwnd, GWL_STYLE)
        style = (style & ~WS_CHILD) | WS_POPUP
        self._u32.SetWindowLongPtrW(self._bar_hwnd, GWL_STYLE, style)
        self._promote_bar_topmost(frame_changed=True)

    def _promote_bar_topmost(self, frame_changed: bool = False) -> None:
        """通过标准 SetWindowPos(HWND_TOPMOST) 确保悬浮条位于系统 TOPMOST 层且不落后于任务栏。"""
        if not self._bar_hwnd:
            return
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | (SWP_FRAMECHANGED if frame_changed else 0)
        self._u32.SetWindowPos(self._bar_hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
        if not (self._u32.GetWindowLongPtrW(self._bar_hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST):
            self._u32.SetWindowPos(
                self._bar_hwnd,
                HWND_NOTOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
            self._u32.SetWindowPos(self._bar_hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)

    def _map_screen_to_window(self, target_hwnd: Optional[wintypes.HWND], rc: RectTuple) -> Tuple[int, int, int, int]:
        pt = (POINT * 2)(POINT(rc.left, rc.top), POINT(rc.right, rc.bottom))
        if target_hwnd:
            self._u32.MapWindowPoints(None, target_hwnd, ctypes.byref(pt), 2)
        return pt[0].x, pt[0].y, pt[1].x - pt[0].x, pt[1].y - pt[0].y

    def _screen_to_parent_client(self, rc: RectTuple) -> Tuple[int, int, int, int]:
        return self._map_screen_to_window(
            self._parent_hwnd if (self._embedded and self._parent_hwnd) else None,
            rc,
        )

    def _apply_mstask_rect(self, rc: RectTuple) -> None:
        if not self._h_rebar or not self._h_mstask:
            return
        mx, my, mw, mh = self._map_screen_to_window(self._h_rebar, rc)
        self._u32.SetWindowPos(
            self._h_mstask,
            None,
            mx,
            my,
            mw,
            mh,
            SWP_NOACTIVATE | SWP_NOZORDER | SWP_ASYNCWINDOWPOS,
        )

    def _is_sibling_widget_rect(
        self,
        rc: Optional[RectTuple],
        rc_tray: Optional[RectTuple],
        scale: float,
    ) -> bool:
        if rc is None or rc.width < 16 or rc.height < 12:
            return False
        if rc_tray is None:
            return True
        is_horizontal = rc_tray.width >= rc_tray.height
        if is_horizontal:
            max_w = max(round(200 * scale), round(rc_tray.width * 0.60))
            min_x = rc_tray.left + round(rc_tray.width * 0.25)
            return (
                rc.top >= rc_tray.top - 4
                and rc.bottom <= rc_tray.bottom + 4
                and 12 <= rc.height <= rc_tray.height + 6
                and 16 <= rc.width < max_w
                and rc.left >= min_x
            )
        max_h = max(round(200 * scale), round(rc_tray.height * 0.60))
        min_y = rc_tray.top + round(rc_tray.height * 0.25)
        return (
            rc.left >= rc_tray.left - 4
            and rc.right <= rc_tray.right + 4
            and 12 <= rc.width <= rc_tray.width + 6
            and 16 <= rc.height < max_h
            and rc.top >= min_y
        )

    def _collect_child_siblings(
        self,
        parent: Optional[wintypes.HWND],
        rc_tray: Optional[RectTuple],
        scale: float,
        seen: set,
        hwnds_out: List[int],
        rects_out: List[RectTuple],
        restrict_tray_zone: bool = True,
    ) -> None:
        if not parent or not self._u32.IsWindow(parent):
            return
        child = self._u32.GetWindow(parent, GW_CHILD)
        while child:
            hwnd_val = int(child)
            if hwnd_val not in seen and child != self._bar_hwnd and child != self._msg_hwnd:
                if self._u32.IsWindowVisible(child) and not self._u32.IsIconic(child):
                    cls_name = self._get_window_class(child)
                    if cls_name not in self._WIN11_IGNORED_SIBLING_CLASSES:
                        rc = self._get_window_rect(child)
                        if self._is_sibling_widget_rect(rc, rc_tray if restrict_tray_zone else None, scale):
                            seen.add(hwnd_val)
                            hwnds_out.append(hwnd_val)
                            rects_out.append(rc)
            child = self._u32.GetWindow(child, GW_HWNDNEXT)

    def _try_read_cached_siblings(
        self,
        rc_tray: Optional[RectTuple],
        scale: float,
        now: float,
    ) -> Optional[List[RectTuple]]:
        """高速缓存通道：未到期且句柄仍有效时直接对已知 HWND 读 GetWindowRect，跳过全量遍历。"""
        if (now - self._last_sibling_scan_ts) >= SIBLING_RESCAN_INTERVAL_SEC:
            return None
        if not self._cached_sibling_hwnds:
            return []

        rects: List[RectTuple] = []
        for hwnd_val in self._cached_sibling_hwnds:
            hwnd = wintypes.HWND(hwnd_val)
            if not self._u32.IsWindow(hwnd) or not self._u32.IsWindowVisible(hwnd) or self._u32.IsIconic(hwnd):
                return None
            rc = self._get_window_rect(hwnd)
            if not self._is_sibling_widget_rect(rc, rc_tray, scale):
                return None
            rects.append(rc)
        return rects

    def _find_taskbar_siblings(
        self,
        rc_tray: Optional[RectTuple],
        scale: float,
        include_rebar: bool,
        force_rescan: bool = False,
    ) -> List[RectTuple]:
        """统一收集 ReBar 子窗、Shell_TrayWnd 子窗与同区顶层 TOOLWINDOW（含鼠标穿透 TM），并缓存 HWND。"""
        now = time.monotonic()
        if not force_rescan:
            cached = self._try_read_cached_siblings(rc_tray, scale, now)
            if cached is not None:
                return cached

        hwnds: List[int] = []
        rects: List[RectTuple] = []
        seen: set = set()
        if include_rebar:
            self._collect_child_siblings(
                self._h_rebar, rc_tray, scale, seen, hwnds, rects, restrict_tray_zone=(rc_tray is not None)
            )
        if rc_tray is not None:
            self._collect_child_siblings(
                self._h_tray, rc_tray, scale, seen, hwnds, rects, restrict_tray_zone=True
            )

            def enum_cb(hwnd: wintypes.HWND, _lparam: wintypes.LPARAM) -> bool:
                hwnd_val = int(hwnd)
                if hwnd_val in seen or hwnd in (self._bar_hwnd, self._msg_hwnd, self._h_tray):
                    return True
                if not self._u32.IsWindowVisible(hwnd) or self._u32.IsIconic(hwnd):
                    return True
                ex = self._u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
                if not (ex & WS_EX_TOOLWINDOW):
                    return True
                if self._get_window_class(hwnd) in self._WIN11_IGNORED_SIBLING_CLASSES:
                    return True
                rc = self._get_window_rect(hwnd)
                if self._is_sibling_widget_rect(rc, rc_tray, scale):
                    seen.add(hwnd_val)
                    hwnds.append(hwnd_val)
                    rects.append(rc)
                return True

            cb_ref = WNDENUMPROC(enum_cb)
            self._u32.EnumWindows(cb_ref, 0)

        if rects:
            self._empty_sibling_rescan_streak = 0
            self._last_nonempty_siblings = list(rects)
            self._cached_sibling_hwnds = hwnds
            self._last_sibling_scan_ts = now
            return rects

        if self._last_nonempty_siblings:
            still_exists = any(
                self._u32.IsWindow(wintypes.HWND(h)) for h in self._cached_sibling_hwnds
            )
            self._empty_sibling_rescan_streak += 1
            if still_exists or self._empty_sibling_rescan_streak < 8:
                # 第三方组件在开始菜单/搜索弹出瞬间可能短时隐藏，保留上次位置并允许下轮立即复扫，避免窗口乱跳
                self._last_sibling_scan_ts = 0.0
                return list(self._last_nonempty_siblings)

        self._empty_sibling_rescan_streak = 0
        self._last_nonempty_siblings = []
        self._cached_sibling_hwnds = []
        self._last_sibling_scan_ts = now
        return []

    def _find_rebar_sibling_widgets(
        self,
        rc_tray: Optional[RectTuple] = None,
        scale: float = 1.0,
        force_rescan: bool = False,
    ) -> List[RectTuple]:
        """收集 Win10 任务栏及 ReBar 内其他第三方工具窗口（如 TrafficMonitor）。"""
        return self._find_taskbar_siblings(
            rc_tray=rc_tray, scale=scale, include_rebar=True, force_rescan=force_rescan
        )

    def _find_win11_sibling_widgets(
        self,
        rc_tray: RectTuple,
        scale: float,
        force_rescan: bool = False,
    ) -> List[RectTuple]:
        """收集任务栏内与悬浮工具条窗口（兼容 Win10 叠加层与 Win11），避免与 TrafficMonitor 重叠。"""
        return self._find_taskbar_siblings(
            rc_tray=rc_tray,
            scale=scale,
            include_rebar=bool(self._h_rebar and self._u32.IsWindow(self._h_rebar)),
            force_rescan=force_rescan,
        )

    def _get_dwm_attr_fn(self):
        """惰性加载 dwmapi.DwmGetWindowAttribute，用于识别系统后台 Cloaked 窗口。"""
        if not self._dwm_attr_initialized:
            self._dwm_attr_initialized = True
            try:
                dwm = ctypes.WinDLL("dwmapi", use_last_error=False)
                fn = dwm.DwmGetWindowAttribute
                fn.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, ctypes.DWORD]
                fn.restype = ctypes.c_long
                self._dwm_attr_fn = fn
            except (OSError, AttributeError):
                self._dwm_attr_fn = None
        return self._dwm_attr_fn

    def _is_window_cloaked(self, hwnd: wintypes.HWND) -> bool:
        """判断窗口是否处于 DWM Cloaked 状态（后台挂起的 UWP 容器等）。"""
        fn = self._get_dwm_attr_fn()
        if fn is None or not hwnd:
            return False
        try:
            cloaked = ctypes.c_int(0)
            hr = fn(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            return hr == 0 and cloaked.value != 0
        except (ctypes.ArgumentError, OSError, TypeError, ValueError):
            return False

    def _is_window_above(self, target_hwnd: wintypes.HWND, ref_hwnd: wintypes.HWND) -> bool:
        """判断 target_hwnd 在 Z 序上是否位于 ref_hwnd（如任务栏）之前。"""
        if not target_hwnd or not ref_hwnd:
            return False
        cur = self._u32.GetWindow(ref_hwnd, GW_HWNDPREV)
        steps = 0
        while cur and steps < 256:
            if cur == target_hwnd:
                return True
            cur = self._u32.GetWindow(cur, GW_HWNDPREV)
            steps += 1
        return False

    def _is_fullscreen_covered(self, rc_tray: RectTuple) -> bool:
        """检测任务栏所在显示器是否有真实可见的非系统全屏窗口正在前景覆盖任务栏。"""
        fg = self._u32.GetForegroundWindow()
        if not fg or fg in (self._h_tray, self._bar_hwnd, self._msg_hwnd):
            return False
        if not self._h_tray:
            return False
        if not self._u32.IsWindowVisible(fg):
            return False
        if self._get_window_class(fg) in self._FULLSCREEN_IGNORED_CLASSES:
            return False
        if self._u32.GetWindowLongPtrW(fg, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return False
        if self._is_window_cloaked(fg):
            return False
        mon_tray = self._u32.MonitorFromWindow(self._h_tray, MONITOR_DEFAULTTOPRIMARY)
        mon_fg = self._u32.MonitorFromWindow(fg, MONITOR_DEFAULTTOPRIMARY)
        if not mon_tray or mon_tray != mon_fg:
            return False
        rc_mon = self._get_monitor_rect(self._h_tray)
        rc_fg = self._get_window_rect(fg)
        if rc_mon is None or rc_fg is None:
            return False
        covers_monitor = (
            rc_fg.left <= rc_mon.left
            and rc_fg.top <= rc_mon.top
            and rc_fg.right >= rc_mon.right
            and rc_fg.bottom >= rc_mon.bottom
        )
        if not covers_monitor:
            return False
        return self._is_window_above(fg, self._h_tray)

    def _is_bar_behind_taskbar(self) -> bool:
        """判断悬浮条是否在 Z 序上落到了任务栏之下（如用户点击任务栏后）。"""
        if not self._bar_hwnd or not self._h_tray:
            return False
        ex = self._u32.GetWindowLongPtrW(self._bar_hwnd, GWL_EXSTYLE)
        if not (ex & WS_EX_TOPMOST):
            return True
        cur = self._u32.GetWindow(self._h_tray, GW_HWNDNEXT)
        steps = 0
        while cur and steps < 1024:
            if cur == self._bar_hwnd:
                return True
            cur = self._u32.GetWindow(cur, GW_HWNDNEXT)
            steps += 1
        return False

    def _sync_layout(self, force: bool = False) -> bool:
        """同步任务栏与自身窗口坐标，返回是否需要重绘位图。"""
        if not self._bar_hwnd:
            return False
        if not self._h_tray or not self._u32.IsWindow(self._h_tray):
            self._attach_to_taskbar()
            if not self._h_tray:
                return False

        # 若系统从经典 ReBar 切换或重新创建（如安装/卸载主题工具），及时重新附着
        if not self._win11_overlay and self._h_mstask and not self._u32.IsWindow(self._h_mstask):
            self._attach_to_taskbar()

        rc_tray = self._get_window_rect(self._h_tray)
        if rc_tray is None:
            return False

        rc_notify = (
            self._get_window_rect(self._h_notify)
            if self._h_notify and self._u32.IsWindow(self._h_notify)
            else None
        )
        dpi = self._get_dpi_for_hwnd(self._h_tray)
        scale = dpi / 96.0

        if self._win11_overlay:
            return self._sync_win11_layout(force, rc_tray, rc_notify, dpi, scale)

        rc_rebar = (
            self._get_window_rect(self._h_rebar)
            if self._h_rebar and self._u32.IsWindow(self._h_rebar)
            else None
        )
        rc_mstask = (
            self._get_window_rect(self._h_mstask)
            if self._h_mstask and self._u32.IsWindow(self._h_mstask)
            else None
        )
        if rc_notify is None:
            return False

        return self._sync_classic_layout(force, rc_tray, rc_notify, rc_rebar, rc_mstask, dpi, scale)

    def _sync_classic_layout(
        self,
        force: bool,
        rc_tray: RectTuple,
        rc_notify: RectTuple,
        rc_rebar: Optional[RectTuple],
        rc_mstask: Optional[RectTuple],
        dpi: int,
        scale: float,
    ) -> bool:
        is_horizontal = rc_tray.width >= rc_tray.height
        anchor_edge = (
            rc_rebar.right if (is_horizontal and rc_rebar is not None) else
            rc_rebar.bottom if (not is_horizontal and rc_rebar is not None) else
            (rc_notify.left if is_horizontal else rc_notify.top)
        )
        if anchor_edge != self._last_anchor_edge:
            self._last_pushed_edge = None
            self._last_anchor_edge = anchor_edge

        target_h = rc_rebar.height if (is_horizontal and rc_rebar is not None) else (
            rc_notify.height if is_horizontal else max(28, round(40 * scale))
        )
        bar_w, bar_h = self._measure_widget_fn(dpi, target_h)
        siblings = self._find_rebar_sibling_widgets(rc_tray=rc_tray, scale=scale, force_rescan=force)
        preview = compute_win10_layout(
            rc_tray=rc_tray,
            rc_notify=rc_notify,
            rc_rebar=rc_rebar,
            bar_w=bar_w,
            bar_h=bar_h,
            scale=scale,
            rc_mstask=rc_mstask,
            sibling_widgets=siblings,
            last_pushed_edge=self._last_pushed_edge,
            reclaim_gap=force,
            placement=self._taskbar_placement,
        )
        bar_shifted_right = (
            self._last_bar_left is not None and preview.bar_rect.left > self._last_bar_left + 1
        )
        layout = (
            compute_win10_layout(
                rc_tray=rc_tray,
                rc_notify=rc_notify,
                rc_rebar=rc_rebar,
                bar_w=bar_w,
                bar_h=bar_h,
                scale=scale,
                rc_mstask=rc_mstask,
                sibling_widgets=siblings,
                last_pushed_edge=self._last_pushed_edge,
                reclaim_gap=True,
                placement=self._taskbar_placement,
            )
            if (bar_shifted_right and not force)
            else preview
        )
        self._last_pushed_edge = layout.pushed_edge
        self._last_bar_left = layout.bar_rect.left

        if layout.need_resize_mstask and layout.mstask_rect and self._h_mstask:
            self._modified_mstask = True
            mx, my, mw, mh = self._screen_to_parent_client(layout.mstask_rect)
            self._u32.SetWindowPos(
                self._h_mstask,
                None,
                mx,
                my,
                mw,
                mh,
                SWP_NOACTIVATE | SWP_NOZORDER | SWP_ASYNCWINDOWPOS,
            )

        bx, by, bw, bh = self._screen_to_parent_client(layout.bar_rect)
        rc_self = self._get_window_rect(self._bar_hwnd)
        pos_changed = force or rc_self is None or (
            abs(rc_self.left - layout.bar_rect.left) > 1
            or abs(rc_self.top - layout.bar_rect.top) > 1
            or rc_self.width != bw
            or rc_self.height != bh
        )
        size_changed = force or (dpi != self._current_dpi) or (self._cached_size != (bw, bh))
        need_zorder = self._is_classic_bar_behind_mstask()
        self._current_dpi = dpi
        self._cached_size = (bw, bh)

        if pos_changed or need_zorder:
            z_insert = HWND_TOP if self._embedded else HWND_TOPMOST
            flags = SWP_NOACTIVATE | SWP_SHOWWINDOW
            if not need_zorder:
                flags |= SWP_NOZORDER
            if not pos_changed:
                flags |= SWP_NOMOVE | SWP_NOSIZE
            self._u32.SetWindowPos(
                self._bar_hwnd,
                z_insert,
                bx,
                by,
                bw,
                bh,
                flags,
            )
        return size_changed or pos_changed

    def _is_classic_bar_behind_mstask(self) -> bool:
        """若 Win10 ReBarWindow32 内 MSTaskSwWClass 的 Z 序跑到了电量条之上则返回 True。"""
        if not self._embedded or not self._h_rebar or not self._h_mstask or not self._bar_hwnd:
            return False
        child = self._u32.GetWindow(self._h_rebar, GW_CHILD)
        while child:
            if child == self._bar_hwnd:
                return False
            if child == self._h_mstask:
                return True
            child = self._u32.GetWindow(child, GW_HWNDNEXT)
        return False

    def _is_win11_bar_behind_children(self) -> bool:
        """若 Win11 Shell_TrayWnd 内 XAML Bridge 等系统子窗跑到了电量条之上则返回 True。"""
        if not self._embedded or not self._h_tray or not self._bar_hwnd:
            return False
        child = self._u32.GetWindow(self._h_tray, GW_CHILD)
        while child:
            if child == self._bar_hwnd:
                return False
            if self._u32.IsWindowVisible(child):
                cls_name = self._get_window_class(child)
                if cls_name in (
                    "Windows.UI.Composition.DesktopWindowContentBridge",
                    "ReBarWindow32",
                    "TrayNotifyWnd",
                ):
                    return True
            child = self._u32.GetWindow(child, GW_HWNDNEXT)
        return False

    def _sync_win11_layout(
        self,
        force: bool,
        rc_tray: RectTuple,
        rc_notify: Optional[RectTuple],
        dpi: int,
        scale: float,
    ) -> bool:
        rc_mon = self._get_monitor_rect(self._h_tray)
        is_horizontal = rc_tray.width >= rc_tray.height
        target_h = rc_tray.height if is_horizontal else max(28, round(40 * scale))
        bar_w, bar_h = self._measure_widget_fn(dpi, target_h)
        siblings = self._find_win11_sibling_widgets(rc_tray, scale, force_rescan=force)

        bar_rect = compute_win11_layout(
            rc_tray=rc_tray,
            rc_notify=rc_notify,
            bar_w=bar_w,
            bar_h=bar_h,
            scale=scale,
            sibling_widgets=siblings,
            screen_rect=rc_mon,
            placement=self._taskbar_placement,
        )
        hidden = (
            bar_rect is None
            or not self._u32.IsWindowVisible(self._h_tray)
            or (not self._embedded and self._is_fullscreen_covered(rc_tray))
        )
        if hidden:
            if not self._bar_hidden_by_cover or self._u32.IsWindowVisible(self._bar_hwnd):
                self._u32.ShowWindow(self._bar_hwnd, SW_HIDE)
                self._bar_hidden_by_cover = True
            return False

        was_hidden = self._bar_hidden_by_cover or not self._u32.IsWindowVisible(self._bar_hwnd)
        self._bar_hidden_by_cover = False

        rc_self = self._get_window_rect(self._bar_hwnd)
        pos_changed = force or was_hidden or rc_self is None or (
            abs(rc_self.left - bar_rect.left) > 1
            or abs(rc_self.top - bar_rect.top) > 1
            or rc_self.width != bar_rect.width
            or rc_self.height != bar_rect.height
        )
        size_changed = force or was_hidden or (dpi != self._current_dpi) or (self._cached_size != (bar_rect.width, bar_rect.height))

        if self._embedded and self._parent_hwnd:
            bx, by, bw, bh = self._screen_to_parent_client(bar_rect)
            missing_topmost = False
            need_zorder = was_hidden or self._is_win11_bar_behind_children()
            z_insert = HWND_TOP
        else:
            bx, by, bw, bh = bar_rect.left, bar_rect.top, bar_rect.width, bar_rect.height
            missing_topmost = not (self._u32.GetWindowLongPtrW(self._bar_hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)
            need_zorder = (
                was_hidden
                or missing_topmost
                or self._u32.GetForegroundWindow() == self._h_tray
                or self._is_bar_behind_taskbar()
            )
            z_insert = HWND_TOPMOST

        self._current_dpi = dpi
        self._cached_size = (bar_rect.width, bar_rect.height)

        if pos_changed or need_zorder:
            if missing_topmost:
                self._u32.SetWindowPos(
                    self._bar_hwnd,
                    HWND_NOTOPMOST,
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
                )
            flags = SWP_NOACTIVATE | (SWP_SHOWWINDOW if was_hidden else 0)
            if not need_zorder:
                flags |= SWP_NOZORDER
            if not pos_changed:
                flags |= SWP_NOMOVE | SWP_NOSIZE
            self._u32.SetWindowPos(
                self._bar_hwnd,
                z_insert,
                bx,
                by,
                bw,
                bh,
                flags,
            )
            if self._tooltip_hwnd:
                self._u32.SetWindowPos(
                    self._tooltip_hwnd,
                    HWND_TOPMOST,
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
                )
        return size_changed or pos_changed

    def _restore_taskbar_layout(self) -> None:
        """退出或切回托盘时按需恢复 Win10 MSTaskSwWClass 宽度；若右侧存在兄弟组件则跳过避免重叠。"""
        if not self._modified_mstask or not self._h_rebar or not self._h_mstask:
            self._modified_mstask = False
            return
        if not (self._u32.IsWindow(self._h_rebar) and self._u32.IsWindow(self._h_mstask)):
            self._modified_mstask = False
            return
        rc_rebar = self._get_window_rect(self._h_rebar)
        rc_mstask = self._get_window_rect(self._h_mstask)
        if not rc_rebar or not rc_mstask:
            self._modified_mstask = False
            return

        is_horizontal = rc_rebar.width >= rc_rebar.height
        rc_tray = self._get_window_rect(self._h_tray) if self._h_tray else None
        scale = self._current_dpi / 96.0
        siblings = self._find_rebar_sibling_widgets(rc_tray=rc_tray, scale=scale)
        half_line = (
            rc_rebar.left + rc_rebar.width // 2
            if is_horizontal
            else rc_rebar.top + rc_rebar.height // 2
        )
        if any((s.right if is_horizontal else s.bottom) > half_line for s in siblings):
            self._modified_mstask = False
            return

        if is_horizontal and rc_rebar.right > rc_mstask.left:
            restored = RectTuple(rc_mstask.left, rc_mstask.top, rc_rebar.right, rc_mstask.bottom)
        elif not is_horizontal and rc_rebar.bottom > rc_mstask.top:
            restored = RectTuple(rc_mstask.left, rc_mstask.top, rc_mstask.right, rc_rebar.bottom)
        else:
            self._modified_mstask = False
            return
        self._apply_mstask_rect(restored)
        self._modified_mstask = False

    def redraw(self) -> None:
        """渲染最新电量图标与文字并提交到常驻条窗口图层。"""
        if not self._bar_hwnd or self._bar_hidden_by_cover:
            return
        self._last_theme_light = self._is_light_theme_fn()
        _w, h = self._cached_size
        dpi = self._current_dpi

        if not self._use_colorkey_fallback:
            img = self._render_widget_fn(dpi, h, self._hovered, None)
            if self._present_ulw(img):
                return
            # 某些环境跨进程 WS_CHILD 不支持 UpdateLayeredWindow 时自动切换到 LWA_COLORKEY
            self._use_colorkey_fallback = True

        light = self._is_light_theme_fn()
        if self._last_colorkey_light != light:
            key_rgb = (242, 242, 242) if light else (1, 1, 1)
            colorkey_ref = key_rgb[0] | (key_rgb[1] << 8) | (key_rgb[2] << 16)
            self._u32.SetLayeredWindowAttributes(self._bar_hwnd, colorkey_ref, 0, LWA_COLORKEY)
            self._last_colorkey_light = light

        fallback_bg = (242, 242, 242) if light else (1, 1, 1)
        img = self._render_widget_fn(dpi, h, self._hovered, fallback_bg)
        screen_dc = self._u32.GetDC(None)
        try:
            hbmp, _, _ = create_hbitmap_from_rgba(self._g32, screen_dc, img)
            if self._cached_fallback_bmp:
                self._g32.DeleteObject(self._cached_fallback_bmp)
            self._cached_fallback_bmp = hbmp
        finally:
            if screen_dc:
                self._u32.ReleaseDC(None, screen_dc)
        self._u32.InvalidateRect(self._bar_hwnd, None, False)

    def _present_ulw(self, img: Image.Image) -> bool:
        if not self._bar_hwnd:
            return False
        screen_dc = self._u32.GetDC(None)
        mem_dc = self._g32.CreateCompatibleDC(screen_dc)
        hbmp = None
        old_obj = None
        try:
            hbmp, w, h = create_hbitmap_from_rgba(self._g32, screen_dc, img)
            old_obj = self._g32.SelectObject(mem_dc, hbmp)

            pt_src = POINT(0, 0)
            sz = SIZE(w, h)
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            # 位置已由 SetWindowPos 精确设置，pptDst 传 None 以避免跨进程 SetParent 坐标系歧义
            ok = self._u32.UpdateLayeredWindow(
                self._bar_hwnd,
                screen_dc,
                None,
                ctypes.byref(sz),
                mem_dc,
                ctypes.byref(pt_src),
                0,
                ctypes.byref(blend),
                ULW_ALPHA,
            )
            return bool(ok)
        finally:
            if old_obj:
                self._g32.SelectObject(mem_dc, old_obj)
            if hbmp:
                self._g32.DeleteObject(hbmp)
            if mem_dc:
                self._g32.DeleteDC(mem_dc)
            if screen_dc:
                self._u32.ReleaseDC(None, screen_dc)

    def post_refresh_ui(self) -> None:
        if self._msg_hwnd:
            self._u32.PostMessageW(self._msg_hwnd, WM_APP_REFRESH_UI, 0, 0)

    def enqueue_balloon(self, title: str, message: str) -> None:
        self._pending_alerts.append((title, message))
        if self._msg_hwnd:
            self._u32.PostMessageW(self._msg_hwnd, WM_APP_NOTIFY, 0, 0)

    def request_quit(self) -> None:
        if self._msg_hwnd:
            self._u32.PostMessageW(self._msg_hwnd, WM_APP_QUIT, 0, 0)

    def _get_tray_icon_size(self) -> int:
        """获取系统托盘小图标 (SM_CXSMICON) 精确像素尺寸以匹配当前任务栏 DPI。"""
        target_hwnd = self._h_tray or self._u32.FindWindowW("Shell_TrayWnd", None) or self._msg_hwnd
        dpi = self._get_dpi_for_hwnd(target_hwnd)
        try:
            if hasattr(self._u32, "GetSystemMetricsForDpi"):
                px = int(self._u32.GetSystemMetricsForDpi(SM_CXSMICON, dpi))
                if px > 0:
                    return max(16, px)
        except Exception:
            pass
        try:
            px = int(self._u32.GetSystemMetrics(SM_CXSMICON))
            if px > 0:
                return max(16, px)
        except Exception:
            pass
        return max(16, round(16 * dpi / 96.0))

    def _add_or_update_tray_icon(self) -> None:
        if not self._msg_hwnd:
            return
        icon_size = self._get_tray_icon_size()
        new_hicon = create_hicon_from_image(self._u32, self._g32, self._get_tray_icon_img(icon_size))
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._msg_hwnd
        nid.uID = TRAY_ICON_ID
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_APP_TRAY_CALLBACK
        nid.hIcon = new_hicon
        nid.szTip = self._get_tooltip_text()[:120]

        cmd = NIM_MODIFY if self._tray_active else NIM_ADD
        ok = self._s32.Shell_NotifyIconW(cmd, ctypes.byref(nid))
        if not ok and self._tray_active:
            ok = self._s32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        if ok:
            self._tray_active = True
            if self._tray_hicon:
                self._u32.DestroyIcon(self._tray_hicon)
            self._tray_hicon = new_hicon
        elif new_hicon:
            self._u32.DestroyIcon(new_hicon)

    def _remove_tray_icon(self) -> None:
        if self._tray_active and self._msg_hwnd:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self._msg_hwnd
            nid.uID = TRAY_ICON_ID
            self._s32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            self._tray_active = False
        if self._tray_hicon:
            self._u32.DestroyIcon(self._tray_hicon)
            self._tray_hicon = None

    def _show_balloon_on_ui_thread(self, title: str, message: str) -> None:
        if not self._msg_hwnd:
            return
        if self._display_mode == DisplayMode.TRAY_ICON_ONLY and self._tray_active:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self._msg_hwnd
            nid.uID = TRAY_ICON_ID
            nid.uFlags = NIF_INFO | NIF_TIP
            nid.szTip = self._get_tooltip_text()[:120]
            nid.szInfoTitle = title[:60]
            nid.szInfo = message[:240]
            nid.dwInfoFlags = NIIF_WARNING
            nid.uVersion = 10000
            self._s32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            return

        self._cleanup_balloon_icon()
        self._balloon_hicon = create_hicon_from_image(self._u32, self._g32, self._get_balloon_icon_img())

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._msg_hwnd
        nid.uID = BALLOON_ICON_ID
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_INFO
        nid.uCallbackMessage = WM_APP_BALLOON_CALLBACK
        nid.hIcon = self._balloon_hicon
        nid.szTip = title[:120]
        nid.szInfoTitle = title[:60]
        nid.szInfo = message[:240]
        nid.dwInfoFlags = NIIF_WARNING
        nid.uVersion = 10000

        if self._s32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            self._balloon_active = True
            self._u32.SetTimer(self._msg_hwnd, TIMER_BALLOON_CLEANUP_ID, 12000, None)

    def _cleanup_balloon_icon(self) -> None:
        if self._msg_hwnd:
            self._u32.KillTimer(self._msg_hwnd, TIMER_BALLOON_CLEANUP_ID)
        if self._balloon_active and self._msg_hwnd:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self._msg_hwnd
            nid.uID = BALLOON_ICON_ID
            self._s32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            self._balloon_active = False
        if self._balloon_hicon:
            self._u32.DestroyIcon(self._balloon_hicon)
            self._balloon_hicon = None

    def _show_context_menu(self) -> None:
        menu_owner = self._msg_hwnd
        if not menu_owner:
            return
        callbacks: Dict[int, Callable[[], None]] = {}
        submenus: List[wintypes.HMENU] = []
        next_id = [100]

        def populate(hmenu: wintypes.HMENU, actions: List[TrayMenuAction]) -> None:
            for act in actions:
                if act.label == "---":
                    self._u32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                    continue
                if act.children:
                    sub = self._u32.CreatePopupMenu()
                    submenus.append(sub)
                    populate(sub, act.children)
                    flags = MF_POPUP | (0 if act.enabled else MF_GRAYED | MF_DISABLED)
                    self._u32.AppendMenuW(hmenu, flags, int(sub), act.label)
                    continue

                item_id = next_id[0]
                next_id[0] += 1
                flags = MF_STRING
                if not act.enabled:
                    flags |= MF_GRAYED | MF_DISABLED
                is_checked = bool(act.checked()) if act.checked is not None else False
                if is_checked:
                    flags |= MF_CHECKED
                self._u32.AppendMenuW(hmenu, flags, item_id, act.label)
                if getattr(act, "radio", False):
                    mii = MENUITEMINFOW()
                    mii.cbSize = ctypes.sizeof(MENUITEMINFOW)
                    mii.fMask = MIIM_FTYPE | MIIM_STATE
                    mii.fType = MFT_RADIOCHECK
                    mii.fState = MFS_CHECKED if is_checked else 0
                    self._u32.SetMenuItemInfoW(hmenu, item_id, False, ctypes.byref(mii))
                if act.callback is not None:
                    callbacks[item_id] = act.callback

        root_menu = self._u32.CreatePopupMenu()
        try:
            populate(root_menu, self._build_menu_items())
            pt = POINT()
            self._u32.GetCursorPos(ctypes.byref(pt))
            self._u32.SetForegroundWindow(menu_owner)
            cmd = self._u32.TrackPopupMenuEx(
                root_menu,
                TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY,
                pt.x,
                pt.y,
                menu_owner,
                None,
            )
            self._u32.PostMessageW(menu_owner, WM_NULL, 0, 0)
            if cmd and cmd in callbacks:
                callbacks[cmd]()
        finally:
            for sub in submenus:
                self._u32.DestroyMenu(sub)
            self._u32.DestroyMenu(root_menu)

    def _bar_wnd_proc(self, hwnd, msg, wparam, lparam) -> int:
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg == WM_ERASEBKGND:
            return 1
        if msg == WM_PAINT:
            ps = PAINTSTRUCT()
            hdc = self._u32.BeginPaint(hwnd, ctypes.byref(ps))
            try:
                if self._use_colorkey_fallback and self._cached_fallback_bmp:
                    mem_dc = self._g32.CreateCompatibleDC(hdc)
                    old = self._g32.SelectObject(mem_dc, self._cached_fallback_bmp)
                    w, h = self._cached_size
                    self._g32.BitBlt(hdc, 0, 0, w, h, mem_dc, 0, 0, SRCCOPY)
                    if old:
                        self._g32.SelectObject(mem_dc, old)
                    self._g32.DeleteDC(mem_dc)
            finally:
                self._u32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        if msg == WM_MOUSEMOVE:
            if not self._hovered:
                self._hovered = True
                tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), TME_LEAVE, hwnd, 0)
                self._u32.TrackMouseEvent(ctypes.byref(tme))
                self.redraw()
            return 0
        if msg == WM_MOUSELEAVE:
            if self._hovered:
                self._hovered = False
                self.redraw()
            return 0
        if msg == WM_LBUTTONDBLCLK:
            self._on_double_click()
            return 0
        if msg == WM_RBUTTONUP:
            self._show_context_menu()
            return 0
        return self._u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _msg_wnd_proc(self, hwnd, msg, wparam, lparam) -> int:
        if msg == WM_TIMER:
            if wparam == TIMER_LAYOUT_ID and self._display_mode == DisplayMode.TASKBAR_BAR:
                if not self._bar_hwnd or not self._u32.IsWindow(self._bar_hwnd):
                    self._ensure_bar_and_sync(schedule_post_redraw=False)
                elif self._sync_layout(force=False):
                    self.redraw()
            elif wparam == TIMER_POST_ATTACH_REDRAW_ID:
                self._u32.KillTimer(hwnd, TIMER_POST_ATTACH_REDRAW_ID)
                if self._display_mode == DisplayMode.TASKBAR_BAR and self._bar_hwnd:
                    self._sync_layout(force=True)
                    self.redraw()
                    self.update_tooltip()
            elif wparam == TIMER_BALLOON_CLEANUP_ID:
                self._cleanup_balloon_icon()
            elif wparam == TIMER_DEVICECHANGE_ID:
                self._u32.KillTimer(hwnd, TIMER_DEVICECHANGE_ID)
                if self._on_device_change is not None:
                    self._on_device_change()
            return 0
        if msg == WM_DEVICECHANGE:
            if (
                wparam in (DBT_DEVICEARRIVAL, DBT_DEVICEREMOVECOMPLETE, DBT_DEVNODES_CHANGED)
                and self._on_device_change is not None
            ):
                self._u32.SetTimer(hwnd, TIMER_DEVICECHANGE_ID, DEVICECHANGE_DEBOUNCE_MS, None)
            return 1
        if msg == WM_SETTINGCHANGE or msg == WM_DPICHANGED:
            if self._display_mode == DisplayMode.TASKBAR_BAR:
                light = self._is_light_theme_fn()
                theme_changed = (
                    self._last_theme_light is not None and light != self._last_theme_light
                )
                self._last_theme_light = light
                if msg == WM_DPICHANGED or theme_changed:
                    self._sync_layout(force=True)
                    self.redraw()
                elif self._sync_layout(force=False):
                    self.redraw()
            else:
                self._add_or_update_tray_icon()
            return 0
        if msg == self._wm_taskbar_created and self._wm_taskbar_created != 0:
            if self._display_mode == DisplayMode.TASKBAR_BAR:
                self._ensure_bar_and_sync(schedule_post_redraw=True)
            else:
                self._tray_active = False
                self._add_or_update_tray_icon()
            return 0
        if msg == WM_APP_REFRESH_UI:
            if self._display_mode == DisplayMode.TASKBAR_BAR:
                self._sync_layout(force=False)
                self.redraw()
                self.update_tooltip()
            else:
                self._add_or_update_tray_icon()
            return 0
        if msg == WM_APP_TRAY_CALLBACK:
            if lparam == WM_LBUTTONDBLCLK:
                self._on_double_click()
            elif lparam == WM_RBUTTONUP:
                self._show_context_menu()
            return 0
        if msg == WM_APP_NOTIFY:
            while self._pending_alerts:
                title, body = self._pending_alerts.pop(0)
                self._show_balloon_on_ui_thread(title, body)
            return 0
        if msg == WM_APP_BALLOON_CALLBACK:
            if lparam in (NIN_BALLOONTIMEOUT, NIN_BALLOONUSERCLICK):
                self._cleanup_balloon_icon()
            return 0
        if msg == WM_APP_QUIT or msg == WM_CLOSE:
            self._cleanup_and_destroy()
            return 0
        if msg == WM_DESTROY:
            self._stop_bar_timers()
            self._unregister_device_notifications()
            self._cleanup_balloon_icon()
            self._remove_tray_icon()
            self._destroy_bar_window()
            self._u32.PostQuitMessage(0)
            return 0

        return self._u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _cleanup_and_destroy(self) -> None:
        self._stop_bar_timers()
        self._unregister_device_notifications()
        self._cleanup_balloon_icon()
        self._remove_tray_icon()
        self._destroy_bar_window()
        if self._msg_hwnd:
            hwnd = self._msg_hwnd
            self._msg_hwnd = None
            if self._u32.IsWindow(hwnd):
                self._u32.DestroyWindow(hwnd)
        for cls_name in (self.BAR_CLASS_NAME, self.MSG_CLASS_NAME):
            try:
                self._u32.UnregisterClassW(cls_name, self._hinst)
            except Exception:
                pass

    def run_message_loop(self) -> None:
        msg = MSG()
        while self._u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self._u32.TranslateMessage(ctypes.byref(msg))
            self._u32.DispatchMessageW(ctypes.byref(msg))
