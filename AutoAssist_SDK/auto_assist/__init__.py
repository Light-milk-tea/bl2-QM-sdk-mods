from __future__ import annotations

import math
import time

from mods_base import BoolOption, build_mod, hook
from unrealsdk import find_all, find_class, logging
from unrealsdk.hooks import Block, Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

# 走近弹药、钱、增益饮料、托格代币时自动捡起。枪和装备不捡。
# 背包满了时，只把这次自动捡取的失败提示和物品弹开压掉。
_PICKUP_NAMES = (
    "GD_Ammodrops.Pickups",
    "GD_Ammodrops.Pickups_BossOnly",
    "GD_Currency.A_Item",
    "GD_BuffDrinks.A_Item",
    "GD_Iris_TorgueToken.UsableItems.Pickup_TorgueToken",
)

# 走近能搜的箱子、柜子时自动打开。要花钱或铱星的不打开。
CHECK_INTERVAL = 0.1
_OPENED_COOLDOWN = 3.0

_picking_up = False
_usable_item: UObject | None = None
_next_open_check = 0.0
_opened: dict[int, float] = {}
_reported = False

pickup_option = BoolOption(
    "AutoPickup",
    True,
    "开",
    "关",
    display_name="自动拾取",
    description="走近时自动捡起弹药、钱、增益饮料和托格代币。",
)
open_option = BoolOption(
    "AutoOpen",
    True,
    "开",
    "关",
    display_name="自动开箱",
    description="走近箱子、柜子时自动打开。要花钱或铱星才能开的不打开。",
)


def _distance(origin: UObject, target: UObject) -> float:
    return math.sqrt((target.X - origin.X) ** 2 + (target.Y - origin.Y) ** 2 + (target.Z - origin.Z) ** 2)


def _usable_class() -> UObject:
    global _usable_item
    if _usable_item is None:
        _usable_item = find_class("WillowUsableItem")
    return _usable_item


def _flag_on(value: object) -> bool:
    try:
        value = value[0]  # type: ignore[index]
    except Exception:
        pass
    return value not in (0, False, None)


def _is_auto_pickup(pickup: UObject, view: UObject, max_distance: float) -> bool:
    inventory = getattr(pickup, "Inventory", None)
    if inventory is None or inventory.Class != _usable_class():
        return False
    if _distance(pickup.Location, view) > max_distance:
        return False
    definition = inventory.DefinitionData.ItemDefinition
    if definition is None:
        return False
    name = definition.GetFullDefinitionName()
    return any(part in name for part in _PICKUP_NAMES)


def _on_cooldown(container: UObject, now: float) -> bool:
    opened_at = _opened.get(container._get_address())
    if opened_at is None:
        return False
    if now - opened_at > _OPENED_COOLDOWN:
        _opened.pop(container._get_address(), None)
        return False
    return True


def _can_open(container: UObject, view: UObject, max_distance: float, now: float) -> bool:
    if "Default__" in container.Name or _on_cooldown(container, now):
        return False
    try:
        if _distance(container.Location, view) > max_distance:
            return False
        if not _flag_on(container.bCanBeUsed) or _flag_on(container.bCostsToUse):
            return False
        loot = container.Loot
        return loot is not None and len(loot) > 0
    except Exception:
        return False


def _pickup_nearby(obj: UObject, view: UObject, max_distance: float) -> None:
    global _picking_up
    for pickup in obj.GetWillowGlobals().PickupList:
        if not _is_auto_pickup(pickup, view, max_distance):
            continue
        _picking_up = True
        try:
            obj.PickupPickupable(pickup, False)
        finally:
            _picking_up = False


def _open_nearby(obj: UObject, view: UObject, max_distance: float, now: float) -> None:
    global _reported
    if obj.Pawn is None:
        return
    for container in find_all("WillowInteractiveObject"):
        if not _can_open(container, view, max_distance, now):
            continue
        try:
            container.UseObject(obj.Pawn, None, 0)
        except Exception as ex:
            if not _reported:
                _reported = True
                logging.warning(f"[auto_assist] UseObject failed: {ex!r}")
            continue
        _opened[container._get_address()] = now


# 人物每帧先按设置决定要不要捡。开箱大约每 0.1 秒查一次，箱子本身不会移动。
@hook("WillowGame.WillowPlayerController:PlayerTick", Type.POST)
def on_player_tick(obj: UObject, args: WrappedStruct, ret: object, func: BoundFunction) -> None:
    global _next_open_check
    if not pickup_option.value and not open_option.value:
        return
    willow_globals = obj.GetWillowGlobals()
    if willow_globals is None:
        return
    max_distance = willow_globals.GetGlobalsDefinition().PlayerInteractionDistance
    view = obj.CalcViewActorLocation
    if pickup_option.value:
        _pickup_nearby(obj, view, max_distance)
    now = time.monotonic()
    if open_option.value and now >= _next_open_check:
        _next_open_check = now + CHECK_INTERVAL
        _open_nearby(obj, view, max_distance, now)


# 自动捡取失败时，游戏会弹「捡不起来」并让物品跳一下。这两处只拦住自动捡的那一次。
@hook("WillowGame.WillowPlayerController:ClientDisplayPickupFailedMessage", Type.PRE)
def on_pickup_failed_message(obj: UObject, args: WrappedStruct, ret: object, func: BoundFunction) -> Block | None:
    if _picking_up:
        return Block
    return None


@hook("WillowGame.WillowPickup:FailedPickup", Type.PRE)
def on_failed_pickup(obj: UObject, args: WrappedStruct, ret: object, func: BoundFunction) -> Block | None:
    if _picking_up:
        return Block
    return None


mod = build_mod(
    options=[pickup_option, open_option],
    hooks=[on_player_tick, on_pickup_failed_message, on_failed_pickup],
)
