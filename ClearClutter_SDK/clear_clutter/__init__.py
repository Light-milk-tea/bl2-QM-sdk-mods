from __future__ import annotations

from mods_base import CoopSupport, Game, build_mod, get_pc, keybind
from ui_utils import show_hud_message
from unrealsdk import find_all, logging
from unrealsdk.unreal import UObject

EXPIRE_SECONDS = 0.05


def _class_name(obj: UObject | None) -> str:
    if obj is None:
        return ""
    try:
        return str(obj.Class.Name)
    except Exception:
        return ""


def _in_level(actor: UObject) -> bool:
    try:
        if "Default__" in str(actor.Name) or actor.bDeleteMe:
            return False
        outer = actor.Outer
    except Exception:
        return False
    return outer is not None and _class_name(outer) == "Level"


def _expire(actor: UObject) -> bool:
    if not _in_level(actor):
        return False
    try:
        actor.LifeSpan = EXPIRE_SECONDS
    except Exception:
        return False
    return True


def _is_mission_pickup(pickup: UObject) -> bool:
    if "Mission" in _class_name(pickup):
        return True
    try:
        inventory = pickup.Inventory
    except Exception:
        return False
    return inventory is not None and "Mission" in _class_name(inventory)


def _is_dead_actor(actor: UObject) -> bool:
    if not _in_level(actor):
        return False
    try:
        return bool(actor.bPlayedDeath) or bool(actor.bTearOff)
    except Exception:
        return False


def _clear_pickups(pc: UObject) -> int:
    try:
        willow_globals = pc.GetWillowGlobals()
        pending = [pickup for pickup in willow_globals.PickupList if pickup is not None]
    except Exception as exc:
        logging.error(f"[clear_clutter] 读取掉落物失败: {exc}")
        return 0
    cleared = 0
    for pickup in pending:
        if _is_mission_pickup(pickup):
            continue
        if _expire(pickup):
            cleared += 1
    return cleared


def _clear_named(class_name: str, player: UObject | None) -> int:
    cleared = 0
    try:
        actors = [actor for actor in find_all(class_name, exact=False) if actor is not None]
    except Exception as exc:
        logging.error(f"[clear_clutter] 查找 {class_name} 失败: {exc}")
        return 0
    for actor in actors:
        if actor is player or not _is_dead_actor(actor):
            continue
        try:
            if actor.Driver is not None:
                continue
        except Exception:
            pass
        if _expire(actor):
            cleared += 1
    return cleared


@keybind("ClearGroundClutter", "F4", display_name="清理地上的掉落物和残骸")
def clear_ground_clutter() -> None:
    logging.info("[clear_clutter] 开始清理")
    pc = get_pc()
    if pc is None or getattr(pc, "Pawn", None) is None:
        show_hud_message("清理地上杂物", "现在不在可清理的场景里")
        return
    player = pc.Pawn
    pickups = _clear_pickups(pc)
    corpses = _clear_named("WillowAIPawn", player)
    wrecks = _clear_named("WillowVehicle", player)
    logging.info(f"[clear_clutter] pickups={pickups} corpses={corpses} wrecks={wrecks}")
    show_hud_message(
        "清理地上杂物",
        f"掉落物 {pickups}，残骸 {corpses}，载具 {wrecks}",
    )


build_mod(
    supported_games=Game.BL2,
    coop_support=CoopSupport.ClientSide,
)
