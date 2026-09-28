from __future__ import annotations

from math import sqrt

from mods_base import get_pc, hook, keybind
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct


_removed_marker = False
_map_objects: list[UObject] = []


def _path_name(obj: UObject | None) -> str:
    if obj is None:
        return "None"
    return obj.PathName(obj)


@hook("WillowGame.StatusMenuMapGFxObject:PlaceCustomObjective")
def on_place_marker(obj: UObject, args: WrappedStruct, ret: object, func: BoundFunction) -> None:
    global _removed_marker
    if not _removed_marker:
        _map_objects.clear()
        for entry in obj.MapObjects:
            if entry.ClientInteractiveObject is not None:
                _map_objects.append(entry.ClientInteractiveObject)
            if entry.Vehicle is not None:
                _map_objects.append(entry.Vehicle)
        _removed_marker = True
        return

    _removed_marker = False
    marker_loc: tuple[float, float] | None = None
    for entry in obj.MapObjects:
        if entry.CustomObjectLoc.X != 0.0:
            marker_loc = (entry.CustomObjectLoc.X, entry.CustomObjectLoc.Y)
    if marker_loc is None or not _map_objects:
        _map_objects.clear()
        return

    distances: list[float] = []
    for target in _map_objects:
        target_loc = (target.Location.X, target.Location.Y)
        distances.append(sqrt((target_loc[0] - marker_loc[0]) ** 2 + (target_loc[1] - marker_loc[1]) ** 2))
    nearest = distances.index(min(distances))
    if distances[nearest] <= 500:
        pawn = get_pc().Pawn
        path = _path_name(_map_objects[nearest]).lower()
        if "vehicle" in path and "spawnstation" not in path:
            if _map_objects[nearest].CanEnterVehicle(pawn):
                _map_objects[nearest].DriverEnter(pawn, True)
        elif "fasttravel" in path:
            exit_point = _map_objects[nearest].TeleportDest.ExitPoints[0]
            pawn.Location = (exit_point.Location.X, exit_point.Location.Y, exit_point.Location.Z + 50)
            pawn.Controller.Rotation = (
                exit_point.Rotation.Pitch,
                exit_point.Rotation.Yaw,
                exit_point.Rotation.Roll,
            )
    _map_objects.clear()


@keybind(
    "open_fast_travel",
    "F2",
    display_name="呼出快速旅行界面",
    description="在任何地方打开快速旅行列表。",
)
def open_fast_travel() -> None:
    get_pc().PlayGfxMovieDefinition("UI_FastTravelStation.FastTravelStation_ThirdPerson_Definition")
