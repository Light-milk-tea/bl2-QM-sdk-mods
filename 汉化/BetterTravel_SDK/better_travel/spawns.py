from __future__ import annotations

import json

from mods_base import SETTINGS_DIR, get_pc, hook
from unrealsdk import find_object, logging
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

_SPAWN_FILE = SETTINGS_DIR / "better_travel_spawns.json"

_load_travel = True
_load_tp = True
_save_name = ""


def _map_name() -> str:
    return str(get_pc().WorldInfo.GetStreamingPersistentMapName()).lower()


def _path_name(obj: UObject | None) -> str:
    if obj is None:
        return "None"
    return obj.PathName(obj)


def _save_station(station: UObject) -> None:
    saved: dict[str, object] = {}
    if _SPAWN_FILE.is_file():
        try:
            saved = json.loads(_SPAWN_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logging.error("[出生点与快速旅行] 读取出生点记录失败")
    exit_pos = station.ExitPoints[0].Location
    exit_rot = station.ExitPoints[0].Rotation
    saved[_save_name] = {
        "MapName": _map_name(),
        "SaveStation": _path_name(station),
        "Position": (exit_pos.X, exit_pos.Y, exit_pos.Z),
        "Rotation": (exit_rot.Pitch, exit_rot.Yaw, 0),
    }
    _SPAWN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SPAWN_FILE.write_text(json.dumps(saved, indent=4), encoding="utf-8")


def _apply_saved_spawn(controller: UObject) -> None:
    if not _SPAWN_FILE.is_file():
        logging.info(f"[出生点与快速旅行] 还没有出生点记录 {_SPAWN_FILE}")
        return
    try:
        saved = json.loads(_SPAWN_FILE.read_text(encoding="utf-8"))
        record = saved[_save_name]
        if _map_name() != record["MapName"]:
            return
        station = find_object("Object", record["SaveStation"])
        get_pc().WorldInfo.GRI.ActiveRespawnCheckpointTeleportActor = station
        controller.Pawn.Location = tuple(record["Position"])
        controller.Rotation = tuple(record["Rotation"])
    except Exception:
        logging.error("[出生点与快速旅行] 读取出生点记录失败")


@hook("WillowGame.WillowSaveGameManager:SaveGame")
def on_save_game(obj: UObject, args: WrappedStruct, ret: object, func: BoundFunction) -> None:
    global _save_name
    map_name = _map_name()
    if map_name != "menumap" and not (_load_tp and _load_travel):
        _save_name = str(args.Filename)
        station = get_pc().WorldInfo.GRI.ActiveRespawnCheckpointTeleportActor
        if station is not None:
            _save_station(station)


@hook("WillowGame.WillowPlayerController:ShouldLoadSaveGameOnSpawn")
def on_load_save(obj: UObject, args: WrappedStruct, ret: object, func: BoundFunction) -> None:
    global _load_travel, _load_tp, _save_name
    if args.bIsInitialSpawn or args.bIsClassChange:
        _load_travel = True
        _load_tp = True
        controller = get_pc()
        _save_name = controller.GetSaveGameNameFromid(controller.GetCachedSaveGame().SaveGameId)


@hook("WillowGame.WillowPlayerController:ClientSetPawnLocation")
def on_spawn_travel(obj: UObject, args: WrappedStruct, ret: object, func: BoundFunction) -> None:
    global _load_travel
    if not obj.IsLocalPlayerController():
        return
    if _map_name() in ("loader", "fakeentry"):
        return
    if _load_travel:
        _load_travel = False
        _apply_saved_spawn(obj)


@hook("WillowGame.WillowPlayerController:StopTeleporterSound")
def on_spawn_tp(obj: UObject, args: WrappedStruct, ret: object, func: BoundFunction) -> None:
    global _load_tp
    if _map_name() in ("loader", "fakeentry"):
        return
    if _load_tp:
        _load_tp = False
        _apply_saved_spawn(obj)
