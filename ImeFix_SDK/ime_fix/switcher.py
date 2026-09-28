from __future__ import annotations

import ctypes as ct
import ctypes.wintypes as wt
from collections.abc import Callable

# 游戏窗口在前台时，把输入语言切到「英语(美国)」；切出游戏、关模组或退出游戏时切回原来的。
# 换掉游戏窗口的消息处理函数，只是为了第一时间收到“切进 / 切出游戏”的通知，其余消息原样转交给游戏。

WM_CLOSE = 0x0010
WM_ACTIVATEAPP = 0x001C
WM_INPUTLANGCHANGEREQUEST = 0x0050
WM_NCDESTROY = 0x0082
WM_TIMER = 0x0113
GWL_WNDPROC = -4
GA_ROOT = 2
KLF_SETFORPROCESS = 0x00000100
LANG_EN_US = 0x0409
US_LAYOUT = 0x04090409
HAND_BACK_TIMER = 0x1F2E

LRESULT = wt.LPARAM
WNDPROC = ct.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

_user32 = ct.WinDLL("user32", use_last_error=True)
_kernel32 = ct.WinDLL("kernel32")

# 32 位的 user32 没有 SetWindowLongPtrW，要用 SetWindowLongW。
if ct.sizeof(ct.c_void_p) == 8:
    _get_window_long = _user32.GetWindowLongPtrW
    _set_window_long = _user32.SetWindowLongPtrW
else:
    _get_window_long = _user32.GetWindowLongW
    _set_window_long = _user32.SetWindowLongW
_get_window_long.argtypes = (wt.HWND, ct.c_int)
_get_window_long.restype = ct.c_void_p
_set_window_long.argtypes = (wt.HWND, ct.c_int, ct.c_void_p)
_set_window_long.restype = ct.c_void_p

_user32.CallWindowProcW.argtypes = (ct.c_void_p, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_user32.CallWindowProcW.restype = LRESULT
_user32.DefWindowProcW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_user32.DefWindowProcW.restype = LRESULT
_user32.GetFocus.argtypes = ()
_user32.GetFocus.restype = wt.HWND
_user32.GetForegroundWindow.argtypes = ()
_user32.GetForegroundWindow.restype = wt.HWND
_user32.GetAncestor.argtypes = (wt.HWND, wt.UINT)
_user32.GetAncestor.restype = wt.HWND
_user32.GetWindowThreadProcessId.argtypes = (wt.HWND, ct.POINTER(wt.DWORD))
_user32.GetWindowThreadProcessId.restype = wt.DWORD
_user32.GetClassNameW.argtypes = (wt.HWND, wt.LPWSTR, ct.c_int)
_user32.GetClassNameW.restype = ct.c_int
_user32.GetKeyboardLayout.argtypes = (wt.DWORD,)
_user32.GetKeyboardLayout.restype = wt.HKL
_user32.GetKeyboardLayoutList.argtypes = (ct.c_int, ct.POINTER(wt.HKL))
_user32.GetKeyboardLayoutList.restype = ct.c_int
_user32.ActivateKeyboardLayout.argtypes = (wt.HKL, wt.UINT)
_user32.ActivateKeyboardLayout.restype = wt.HKL
_user32.PostMessageW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_user32.PostMessageW.restype = wt.BOOL
_user32.SetTimer.argtypes = (wt.HWND, ct.c_size_t, wt.UINT, ct.c_void_p)
_user32.SetTimer.restype = ct.c_size_t
_user32.KillTimer.argtypes = (wt.HWND, ct.c_size_t)
_user32.KillTimer.restype = wt.BOOL
_kernel32.GetCurrentThreadId.argtypes = ()
_kernel32.GetCurrentThreadId.restype = wt.DWORD


def language(hkl: int) -> int:
    return (hkl or 0) & 0xFFFF


def current_layout(thread_id: int = 0) -> int:
    return _user32.GetKeyboardLayout(thread_id) or 0


def installed_layouts() -> list[int]:
    count = _user32.GetKeyboardLayoutList(0, None)
    if count <= 0:
        return []
    buffer = (wt.HKL * count)()
    count = _user32.GetKeyboardLayoutList(count, buffer)
    return [hkl or 0 for hkl in buffer[:count]]


def us_layout() -> int:
    layouts = installed_layouts()
    if US_LAYOUT in layouts:
        return US_LAYOUT
    return next((hkl for hkl in layouts if language(hkl) == LANG_EN_US), 0)


def game_window() -> int:
    hwnd = _user32.GetFocus()
    if not hwnd:
        foreground = _user32.GetForegroundWindow()
        if foreground and _user32.GetWindowThreadProcessId(foreground, None) == _kernel32.GetCurrentThreadId():
            hwnd = foreground
    return (_user32.GetAncestor(hwnd, GA_ROOT) or 0) if hwnd else 0


def window_class(hwnd: int) -> str:
    buffer = ct.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def _as_lparam(value: int) -> int:
    bits = 8 * ct.sizeof(wt.LPARAM)
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


class LayoutSwitcher:
    def __init__(self, log: Callable[[str], None]) -> None:
        self.log = log
        self.windows: dict[int, int] = {}
        self.active = False
        self.inside = False
        self.saved = 0
        self.pending = 0
        self.missing_us = False
        self.switches = 0
        self._reported = False
        # 回调对象必须一直留着；被回收的话，窗口再调它会直接崩。
        self._proc = WNDPROC(self._wndproc)
        self._proc_address = ct.cast(self._proc, ct.c_void_p).value

    def attach(self, hwnd: int) -> None:
        if not hwnd or hwnd in self.windows:
            return
        if _get_window_long(hwnd, GWL_WNDPROC) == self._proc_address:
            return
        ct.set_last_error(0)
        previous = _set_window_long(hwnd, GWL_WNDPROC, self._proc_address)
        if not previous:
            self.log(f"attach failed hwnd=0x{hwnd:X} error={ct.get_last_error()}")
            return
        self.windows[hwnd] = previous
        self.log(f"attached hwnd=0x{hwnd:X} class={window_class(hwnd)}")

    # 只有外层还是我们的处理函数时才能换回去；别人又套在外面的话，就留着只转发。
    def detach_all(self) -> None:
        for hwnd, previous in list(self.windows.items()):
            _user32.KillTimer(hwnd, HAND_BACK_TIMER)
            if _get_window_long(hwnd, GWL_WNDPROC) == self._proc_address:
                _set_window_long(hwnd, GWL_WNDPROC, previous)
                del self.windows[hwnd]
            else:
                self.log(f"hwnd=0x{hwnd:X} kept in pass-through mode, another hook is on top")

    def owns_foreground(self) -> bool:
        foreground = _user32.GetForegroundWindow()
        return bool(foreground) and (_user32.GetAncestor(foreground, GA_ROOT) or 0) in self.windows

    # 钩子里定期调用，补上漏掉的“切进 / 切出”通知。
    def poll(self) -> None:
        if not self.active or not self.windows:
            return
        if self.owns_foreground():
            self.enter()
        else:
            self.leave()
            self._hand_back()

    # 每次切进游戏只切一次：在游戏里自己按 Win+空格 换回拼音，不会被再切走。
    def enter(self) -> None:
        if not self.active or self.inside:
            return
        self.inside = True
        self.pending = 0
        self._stop_timer()
        current = current_layout()
        if language(current) == LANG_EN_US:
            return
        target = us_layout()
        if not target:
            if not self.missing_us:
                self.log("no English (US) keyboard layout installed")
            self.missing_us = True
            return
        self.missing_us = False
        if _user32.ActivateKeyboardLayout(target, KLF_SETFORPROCESS):
            self.saved = current
            self.switches += 1
            self.log(f"entered game: 0x{current:08X} -> 0x{target:08X}")
        else:
            self.log(f"ActivateKeyboardLayout(0x{target:08X}) failed, error={ct.get_last_error()}")

    def leave(self) -> None:
        if not self.inside:
            return
        self.inside = False
        if not self.saved:
            return
        saved, self.saved = self.saved, 0
        _user32.ActivateKeyboardLayout(saved, KLF_SETFORPROCESS)
        self.log(f"left game: restored 0x{saved:08X}")
        self.pending = saved
        self._hand_back()
        if self.pending:
            for hwnd in self.windows:
                _user32.SetTimer(hwnd, HAND_BACK_TIMER, 250, None)

    # Windows 默认所有程序共用一个输入法，切出游戏时新的前台程序可能跟着停在英文。
    # 只有它确实停在英文时，才把原来的输入法交还给它。
    def _hand_back(self) -> None:
        if self.pending:
            foreground = _user32.GetForegroundWindow()
            if not foreground or (_user32.GetAncestor(foreground, GA_ROOT) or 0) in self.windows:
                return
            thread = _user32.GetWindowThreadProcessId(foreground, None)
            if language(current_layout(thread)) == LANG_EN_US and language(self.pending) != LANG_EN_US:
                self._request(foreground, self.pending)
                self.log(f"handed 0x{self.pending:08X} back to hwnd=0x{foreground:X}")
            self.pending = 0
        self._stop_timer()

    def _request(self, hwnd: int, hkl: int) -> None:
        _user32.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, _as_lparam(hkl))

    def _stop_timer(self) -> None:
        for hwnd in self.windows:
            _user32.KillTimer(hwnd, HAND_BACK_TIMER)

    def _wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        previous = self.windows.get(hwnd)
        try:
            if msg == WM_ACTIVATEAPP:
                if wparam:
                    self.enter()
                else:
                    self.leave()
            elif msg == WM_TIMER and wparam == HAND_BACK_TIMER:
                self._hand_back()
                return 0
            elif msg in (WM_CLOSE, WM_NCDESTROY):
                self.leave()
                if msg == WM_NCDESTROY and previous is not None:
                    _user32.KillTimer(hwnd, HAND_BACK_TIMER)
                    _set_window_long(hwnd, GWL_WNDPROC, previous)
                    del self.windows[hwnd]
        except Exception as ex:  # noqa: BLE001
            if not self._reported:
                self._reported = True
                self.log(f"wndproc error: {ex!r}")
        if previous is None:
            return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        return _user32.CallWindowProcW(previous, hwnd, msg, wparam, lparam)
