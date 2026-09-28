from __future__ import annotations

import time

from mods_base import ButtonOption, build_mod, hook
from unrealsdk import logging
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

from .switcher import LayoutSwitcher, game_window

# 接管窗口和切换键盘都必须在游戏线程上做，而模组管理器启用模组时不在游戏线程。
# 所以放在主菜单、游戏里每帧会走的钩子，以及游戏收按键的钩子里，每半秒检查一次。
CHECK_INTERVAL = 0.5

_next_check = 0.0


def _log(message: str) -> None:
    logging.info(f"[ime_fix] {message}")


_switcher = LayoutSwitcher(_log)


def _status_text() -> str:
    if not _switcher.active:
        return "模组已关闭，不再自动切换键盘。"
    if _switcher.missing_us:
        return "没找到「英语(美国)」键盘。请先在 Windows 设置 → 时间和语言 → 语言和区域 里添加英语(美国)。"
    if not _switcher.windows:
        return "还没接上游戏窗口，回到游戏画面一两秒后会自动接上。"
    return "已接管：进游戏自动用英语(美国)键盘，切出游戏、关掉模组或退出游戏会换回原来的输入法。"


def _check(force: bool = False) -> None:
    global _next_check
    now = time.monotonic()
    if not force and now < _next_check:
        return
    _next_check = now + CHECK_INTERVAL
    _switcher.attach(game_window())
    _switcher.poll()
    status.description = _status_text()


@hook("WillowGame.FrontendGFxMovie:OnTick", Type.POST)
def on_menu_tick(obj: UObject, args: WrappedStruct, ret: object, func: BoundFunction) -> None:
    _check()


@hook("WillowGame.WillowPlayerController:PlayerTick", Type.POST)
def on_player_tick(obj: UObject, args: WrappedStruct, ret: object, func: BoundFunction) -> None:
    _check()


@hook("WillowGame.WillowUIInteraction:InputKey", Type.PRE)
def on_input_key(obj: UObject, args: WrappedStruct, ret: object, func: BoundFunction) -> None:
    _check()


def on_enable() -> None:
    global _next_check
    _switcher.active = True
    _next_check = 0.0


# 先趁游戏还在前台把输入法换回去，再放开游戏窗口。
def on_disable() -> None:
    _switcher.leave()
    _switcher.active = False
    _switcher.detach_all()
    status.description = _status_text()
    _log(f"disabled after {_switcher.switches} switch(es)")


status = ButtonOption(
    "Status",
    display_name="当前状态",
    description="打开模组后，回到游戏画面一两秒内会自动接管。按一下立刻重新检查，退出这页再进来能看到最新状态。",
    on_press=lambda _: _check(force=True),
)

mod = build_mod(
    hooks=[on_menu_tick, on_player_tick, on_input_key],
    options=[status],
    on_enable=on_enable,
    on_disable=on_disable,
)
