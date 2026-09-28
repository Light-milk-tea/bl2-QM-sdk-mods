from __future__ import annotations

from mods_base import build_mod, get_pc, hook, keybind
from ui_utils import show_chat_message
from unrealsdk import find_all, logging
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

_restore_location = True
_loading = False
_launch_started = False
_difficulty = 0
_overpower_level = 0
_map_name = ""
_coords = [0.0, 0.0, 0.0]
_rotation = [0, 0, 0]


def _feedback(message: str) -> None:
    show_chat_message(message, user="[地图重载]", timestamp=None)


def _store_location() -> None:
    global _map_name
    controller = get_pc()
    _map_name = controller.WorldInfo.GetMapName()
    location = controller.Pawn.Location
    _coords[0] = location.X
    _coords[1] = location.Y
    _coords[2] = location.Z
    rotation = controller.Rotation
    _rotation[0] = rotation.Pitch
    _rotation[1] = rotation.Roll
    _rotation[2] = rotation.Yaw
    logging.info(f"[地图重载] 保存位置 {_map_name} {_coords} {_rotation}")


def _restore_saved_location() -> None:
    controller = get_pc()
    if controller.WorldInfo.GetMapName() != _map_name:
        _feedback("保存的位置不在当前地图，无法恢复位置，请重新保存位置后再试。")
        return
    controller.Pawn.Location.X = _coords[0]
    controller.Pawn.Location.Y = _coords[1]
    controller.Pawn.Location.Z = _coords[2]
    rotation = controller.Rotation
    rotation.Pitch = _rotation[0]
    rotation.Roll = _rotation[1]
    rotation.Yaw = _rotation[2]
    logging.info(f"[地图重载] 恢复位置 {_map_name} {_coords} {_rotation}")
    _feedback("位置已恢复。")


def _reload_current_map(skip_save: bool) -> None:
    global _difficulty, _overpower_level, _loading, _launch_started
    _launch_started = False
    controller = get_pc()
    _difficulty = controller.GetCurrentPlaythrough()
    save = controller.GetCachedSaveGame()
    if save.LastOverpowerChoice and save.NumOverpowerLevelsUnlocked:
        _overpower_level = max(min(save.LastOverpowerChoice, save.NumOverpowerLevelsUnlocked), 0)
    else:
        _overpower_level = -1
    _loading = True
    controller.ReturnToTitleScreen(skip_save, False)


def _frontend_movie() -> UObject | None:
    movies = list(find_all("FrontendGFxMovie"))
    if len(movies) > 1:
        return movies[1]
    return movies[0] if movies else None


@hook("WillowGame.WillowHUD:CreateWeaponScopeMovie")
def on_scope_movie(obj: UObject, args: WrappedStruct, ret: object, func: BoundFunction) -> None:
    global _loading
    if not (_restore_location and _loading):
        return
    controller = get_pc()
    hud = controller.myHUD.HUDMovie
    if controller.Pawn is None or hud is None:
        return
    _restore_saved_location()
    _loading = False


@hook("WillowGame.FrontendGFxMovie:OnTick")
def on_main_menu_tick(obj: UObject, args: WrappedStruct, ret: object, func: BoundFunction) -> None:
    global _loading, _launch_started
    if not _loading or _launch_started:
        return
    try:
        controller = get_pc()
        movie = _frontend_movie()
        if movie is None:
            return
        _launch_started = True
        if _overpower_level != -1:
            controller.OnSelectOverpowerLevel(controller.GetCachedSaveGame(), _overpower_level)
            movie.CurrentSelectedOverpowerLevel = _overpower_level
        logging.info(f"[地图重载] 读取存档 难度 {_difficulty} OP{_overpower_level}")
        if not _restore_location:
            _loading = False
        movie.LaunchSaveGame(_difficulty)
    except Exception as exc:
        logging.error(f"[地图重载] 回到主菜单后读档失败: {exc}")
        _loading = False
        _launch_started = True


@keybind(
    "reload_without_save",
    "F7",
    display_name="快速重载并不保存",
    description="不保存，直接重载当前地图。",
)
def reload_without_save() -> None:
    _reload_current_map(True)


@keybind(
    "reload_and_save",
    "F8",
    display_name="快速重载并保存",
    description="先保存，再重载当前地图。",
)
def reload_and_save() -> None:
    _reload_current_map(False)


@keybind(
    "toggle_restore",
    "F6",
    display_name="启用/禁用位置恢复",
    description="重载之后是否回到保存的位置。",
)
def toggle_restore() -> None:
    global _restore_location
    _restore_location = not _restore_location
    state = "位置恢复已启用。" if _restore_location else "位置恢复已禁用。"
    logging.info(f"[地图重载] {state}")
    _feedback(state)


@keybind("save_location", "F5", display_name="保存位置", description="记下当前坐标，重载后回到这里。")
def save_location() -> None:
    _store_location()
    _feedback("位置已保存。")


mod = build_mod()
