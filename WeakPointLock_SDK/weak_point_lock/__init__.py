"""把玩家枪械的子弹方向改向敌人的暴击骨骼，镜头保持不动。"""

from __future__ import annotations

import math
import time
from typing import Any

from mods_base import (
    BoolOption,
    CoopSupport,
    Game,
    SliderOption,
    build_mod,
    get_pc,
    hook,
    keybind,
)
import unrealsdk
from unrealsdk import logging
from unrealsdk.hooks import Block, Type, add_hook, remove_hook
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

try:
    from unrealsdk.unreal import make_struct
except ImportError:  # pragma: no cover - older sdk layouts
    from unrealsdk import make_struct  # type: ignore[attr-defined,no-redef]

_MISSING = object()
_URU_TO_RAD = math.pi / 32768.0
_RAD_TO_URU = 32768.0 / math.pi
_STICKY_FOV_SCALE = 1.25
_MAX_SHOT_LOGS = 5
_MAX_HIT_LOGS = 3

lock_enabled = BoolOption(
    "LockEnabled",
    True,
    display_name="锁定弱点",
    description="打开后，手持枪的子弹会改向准星附近敌人的暴击骨骼。",
)
fov_degrees = SliderOption(
    "FovDegrees",
    20,
    5,
    45,
    1,
    is_integer=True,
    display_name="视野角",
    description="准星和弱点的夹角超过这个值就不锁定。",
)
max_distance = SliderOption(
    "MaxDistance",
    12000,
    500,
    40000,
    100,
    is_integer=True,
    display_name="最大距离",
    description="超过这个距离不锁定。单位是游戏坐标。",
)
sticky_target = BoolOption(
    "StickyTarget",
    True,
    display_name="粘滞目标",
    description="连射时尽量继续锁同一只敌人。",
)
debug_log = BoolOption(
    "DebugLog",
    True,
    display_name="调试日志",
    description="把试射结果写进 unrealsdk 日志，前缀是 [WPL]。每种信息只记有限几次。",
)

_logged: set[str] = set()
_crit_bones: dict[str, list[str]] = {}
_shaped: set[str] = set()
_shot: dict[str, Any] | None = None
_depth = 0
_shot_logs = 0
_hit_logs = 0
_traces = 0
_sticky: UObject | None = None
_selecting = False
_aim_cache: tuple[UObject, tuple[float, float, float], str, float] | None = None
_pending: list[dict[str, Any]] = []
_saved_rot: list[tuple[Any, int, int, int]] | None = None
_watch: dict[str, Any] | None = None
_manual: list[tuple[str, Type, str]] = []
_hud_sent: set[str] = set()


@keybind("ToggleWeakPoint", "F8", display_name="开关弱点锁定")
def toggle_lock() -> None:
    lock_enabled.value = not bool(lock_enabled.value)
    state = "开" if lock_enabled.value else "关"
    logging.info(f"[WPL] 弱点锁定{state}")
    _hud(f"弱点锁定：{state}", "子弹改向弱点" if lock_enabled.value else "已恢复原版弹道")


def _hud(title: str, message: str) -> None:
    try:
        from ui_utils import show_hud_message

        show_hud_message(title, message)
    except Exception:
        pass


def _hud_once(key: str, title: str, message: str) -> None:
    if key in _hud_sent:
        return
    _hud_sent.add(key)
    _hud(title, message)


def _log_once(key: str, message: str) -> None:
    if not debug_log.value or key in _logged:
        return
    _logged.add(key)
    logging.info(message)


def _class_name(obj: Any) -> str:
    if obj is None:
        return ""
    try:
        return str(obj.Class.Name)
    except Exception:
        return ""


def _obj_key(obj: Any) -> str:
    try:
        return str(obj)
    except Exception:
        return f"id:{id(obj)}"


def _try_get(obj: Any, name: str) -> Any:
    if obj is None:
        return _MISSING
    try:
        return getattr(obj, name)
    except Exception:
        return _MISSING


def _get(obj: Any, name: str) -> Any:
    value = _try_get(obj, name)
    return None if value is _MISSING else value


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _xyz(value: Any) -> tuple[float, float, float] | None:
    if value is None or value is _MISSING:
        return None
    x = _as_float(_try_get(value, "X"))
    y = _as_float(_try_get(value, "Y"))
    z = _as_float(_try_get(value, "Z"))
    if x is None or y is None or z is None:
        return None
    return (x, y, z)


def _length(value: tuple[float, float, float]) -> float:
    return math.sqrt(value[0] * value[0] + value[1] * value[1] + value[2] * value[2])


def _sub(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def _scale(value: tuple[float, float, float], factor: float) -> tuple[float, float, float]:
    return (value[0] * factor, value[1] * factor, value[2] * factor)


def _angle(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    left_len = _length(left)
    right_len = _length(right)
    if left_len < 1e-4 or right_len < 1e-4:
        return 180.0
    dot = (
        left[0] * right[0] + left[1] * right[1] + left[2] * right[2]
    ) / (left_len * right_len)
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(math.acos(dot))


def _rotator_forward(rot: Any) -> tuple[float, float, float] | None:
    pitch = _as_float(_try_get(rot, "Pitch"))
    yaw = _as_float(_try_get(rot, "Yaw"))
    if pitch is None or yaw is None:
        return None
    pitch *= _URU_TO_RAD
    yaw *= _URU_TO_RAD
    cp = math.cos(pitch)
    return (cp * math.cos(yaw), cp * math.sin(yaw), math.sin(pitch))


def _vector(value: tuple[float, float, float]) -> Any:
    return make_struct("Vector", X=value[0], Y=value[1], Z=value[2])


def _write_vector(obj: Any, name: str, value: tuple[float, float, float]) -> bool:
    try:
        setattr(obj, name, _vector(value))
        return True
    except Exception:
        current = _try_get(obj, name)
        if current is _MISSING:
            return False
        try:
            current.X = value[0]
            current.Y = value[1]
            current.Z = value[2]
            return True
        except Exception:
            return False


def _write_rotation(obj: Any, direction: tuple[float, float, float]) -> None:
    yaw = math.atan2(direction[1], direction[0]) * _RAD_TO_URU
    horiz = math.hypot(direction[0], direction[1])
    pitch = math.atan2(direction[2], horiz) * _RAD_TO_URU
    try:
        obj.Rotation = make_struct(
            "Rotator",
            Pitch=int(pitch),
            Yaw=int(yaw),
            Roll=0,
        )
    except Exception:
        return


def _find_all(class_name: str) -> list[Any]:
    try:
        found = unrealsdk.find_all(class_name, exact=True)
    except TypeError:
        found = unrealsdk.find_all(class_name)
    return list(found)


# 不遍历对象的全部属性。pyunrealsdk 读到部分属性类型会直接把游戏打崩。
_PROBE_BONES = (
    "Head",
    "b_Head",
    "Head_M",
    "Neck",
    "b_Neck",
    "LeftShoulder",
    "RightShoulder",
    "LeftArm",
    "RightArm",
    "LeftForeArm",
    "RightForeArm",
    "LeftUpLeg",
    "RightUpLeg",
    "Hips",
    "Pelvis",
    "Spine2",
)


def _describe(obj: Any, _limit: int = 24) -> str:
    return (
        f"class={_class_name(obj)} "
        f"loc={_xyz(_try_get(obj, 'Location'))} "
        f"vel={_xyz(_try_get(obj, 'Velocity'))}"
    )


def _bone_location(mesh: Any, bone: str) -> tuple[float, float, float] | None:
    if mesh is None or not bone:
        return None
    getter = getattr(mesh, "GetBoneLocation", None)
    if not callable(getter):
        return None
    for args in ((bone,), (bone, 0)):
        try:
            point = _xyz(getter(*args))
        except Exception:
            continue
        if point is not None and _length(point) > 1.0:
            return point
    return None


def _pawn_root(pawn: UObject) -> tuple[float, float, float] | None:
    loc = _xyz(_try_get(pawn, "Location"))
    if loc is not None and _length(loc) > 1.0:
        return loc
    mesh = _get(pawn, "Mesh")
    comp = _xyz(_try_get(mesh, "Location")) if mesh is not None else None
    if comp is not None and _length(comp) > 1.0:
        return comp
    return loc


_REGION_FIELDS = (
    "BodyClass",
    "MyBodyClass",
    "HitRegions",
    "RegionList",
    "DamageLocations",
    "HitRegionHelpers",
    "BodyHitRegions",
)
_CRIT_FLAGS = ("bCriticalHit", "bIsCriticalHit", "bCritical")
_BONE_FIELDS = (
    "BoneName",
    "AssociatedBoneName",
    "HitBoneName",
    "BoneNames",
    "AssociatedBones",
    "Bones",
)
_FALLBACK_BONES = (
    "Head",
    "b_Head",
    "Head_M",
    "Neck",
    "b_Neck",
    "LeftShoulder",
    "RightShoulder",
    "LeftArm",
    "RightArm",
    "LeftForeArm",
    "RightForeArm",
    "LeftUpLeg",
    "RightUpLeg",
    "Eye",
    "LeftEye",
    "RightEye",
)


def _clean_bone(value: Any) -> str:
    text = str(value).strip().strip("'\"")
    if not text or text == "None" or text.startswith("<"):
        return ""
    if "'" in text:
        text = text.split("'", 1)[-1].strip("'\"")
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.strip()


def _bone_names_from(value: Any) -> list[str]:
    if value is None or value is _MISSING or isinstance(value, (bool, float)):
        return []
    if isinstance(value, (str, int)):
        bone = _clean_bone(value)
        return [bone] if bone else []
    names: list[str] = []
    try:
        items = list(value)
    except Exception:
        bone = _clean_bone(value)
        return [bone] if bone else []
    for item in items[:12]:
        bone = _clean_bone(item)
        if bone:
            names.append(bone)
    return names


def _is_critical(obj: Any) -> bool:
    for name in _CRIT_FLAGS:
        value = _try_get(obj, name)
        if value is _MISSING:
            continue
        try:
            if bool(value):
                return True
        except Exception:
            continue
    return False


def _region_children(value: Any) -> list[Any]:
    if value is None or value is _MISSING or isinstance(value, (str, int, float, bool)):
        return []
    if _try_get(value, "Class") is not _MISSING:
        return [value]
    children: list[Any] = []
    try:
        for item in value:
            children.append(item)
            if len(children) >= 12:
                break
    except Exception:
        return []
    return children


def _crit_bone_names(pawn: UObject) -> list[str]:
    body = _get(pawn, "BodyClass")
    key = _obj_key(body if body is not None else pawn)
    cached = _crit_bones.get(key)
    if cached is not None:
        return cached
    found: list[str] = []
    seen: set[str] = set()

    def walk(obj: Any, depth: int) -> None:
        if obj is None or obj is _MISSING or depth > 3:
            return
        if _try_get(obj, "Class") is not _MISSING:
            ident = _obj_key(obj)
            if ident in seen:
                return
            seen.add(ident)
        if _is_critical(obj):
            for field in _BONE_FIELDS:
                found.extend(_bone_names_from(_try_get(obj, field)))
        for field in _REGION_FIELDS:
            for child in _region_children(_try_get(obj, field)):
                walk(child, depth + 1)

    walk(pawn, 0)
    unique: list[str] = []
    for bone in found:
        if bone not in unique:
            unique.append(bone)
    _crit_bones[key] = unique
    _log_once(f"regions:{key}", f"[WPL] 弱点骨骼 {unique or '无，改用关节'}")
    return unique


def _weak_point(
    pawn: UObject,
    root: tuple[float, float, float],
    origin: tuple[float, float, float],
    forward: tuple[float, float, float],
) -> tuple[tuple[float, float, float], str]:
    mesh = _get(pawn, "Mesh")
    bones = _crit_bone_names(pawn) or list(_FALLBACK_BONES)
    best: tuple[float, float, float] | None = None
    best_name = "HeadHigh"
    best_angle = 180.0
    for bone in bones:
        point = _bone_location(mesh, bone)
        if point is None or _length(_sub(point, root)) < 8.0:
            continue
        angle = _angle(forward, _sub(point, origin))
        if angle < best_angle:
            best_angle = angle
            best = point
            best_name = bone
    if best is not None:
        return best, best_name
    return (root[0], root[1], root[2] + 72.0), "Chest"


def _best_point(
    pawn: UObject,
    _origin: tuple[float, float, float],
    _forward: tuple[float, float, float],
) -> tuple[tuple[float, float, float], str] | None:
    root = _pawn_root(pawn)
    if root is None or _length(root) <= 1.0:
        return None
    # 先用胸口判断在不在视野里，真正开枪时再抬到头部。
    eye = _as_float(_try_get(pawn, "BaseEyeHeight"))
    if eye is None or eye < 30.0:
        eye = 70.0
    return (root[0], root[1], root[2] + eye * 0.55), "Chest"


def _view_origin(pawn: UObject, pc: UObject | None) -> tuple[float, float, float] | None:
    for owner, name in ((pawn, "GetPawnViewLocation"), (pc, "GetFocalPoint")):
        if owner is None:
            continue
        getter = getattr(owner, name, None)
        if not callable(getter):
            continue
        try:
            point = _xyz(getter())
        except Exception:
            point = None
        if point is not None:
            return point
    base = _xyz(_try_get(pawn, "Location"))
    if base is None:
        return None
    eye = _as_float(_try_get(pawn, "BaseEyeHeight"))
    if eye is None:
        eye = 72.0
    return (base[0], base[1], base[2] + eye)


def _view_forward(pawn: UObject, pc: UObject | None) -> tuple[float, float, float] | None:
    for obj in (pc, _get(pawn, "Controller"), pawn):
        rot = _try_get(obj, "Rotation")
        if rot is _MISSING:
            continue
        forward = _rotator_forward(rot)
        if forward is not None and _length(forward) > 0.5:
            return forward
    return None


def _targetable(pawn: Any, player: UObject) -> bool:
    if pawn is None or pawn == player:
        return False
    text = _obj_key(pawn)
    if "PersistentLevel" not in text or "Default__" in text:
        return False
    cls = _class_name(pawn)
    if "Player" in cls or "AI" not in cls:
        return False
    if _try_get(pawn, "bDeleteMe") is True:
        return False
    return True


def _consider(
    pawn: UObject,
    origin: tuple[float, float, float],
    forward: tuple[float, float, float],
    limit: float,
    reach: float,
) -> tuple[float, float, UObject, tuple[float, float, float], str] | None:
    point_info = _best_point(pawn, origin, forward)
    if point_info is None:
        return None
    point, bone = point_info
    offset = _sub(point, origin)
    distance = _length(offset)
    if distance > reach or distance < 1.0:
        return None
    angle = _angle(forward, offset)
    if angle > limit:
        return None
    return (angle, distance, pawn, point, bone)


def _select(
    player: UObject,
    pc: UObject | None,
) -> tuple[UObject, tuple[float, float, float], str] | None:
    global _sticky, _selecting, _aim_cache
    if _selecting:
        return None
    now = time.monotonic()
    if _aim_cache is not None and now < _aim_cache[3] and _targetable(_aim_cache[0], player):
        return _aim_cache[0], _aim_cache[1], _aim_cache[2]
    _selecting = True
    try:
        origin = _view_origin(player, pc)
        forward = _view_forward(player, pc)
        if origin is None or forward is None:
            _log_once("no-view", "[WPL] 读不到玩家视角")
            return None
        reach = float(max_distance.value)
        limit = max(float(fov_degrees.value), 40.0)
        if sticky_target.value and _sticky is not None and _targetable(_sticky, player):
            held = _consider(_sticky, origin, forward, limit * _STICKY_FOV_SCALE, reach)
            if held is not None:
                root = _pawn_root(held[2])
                if root is not None:
                    head, bone = _weak_point(held[2], root, origin, forward)
                    _aim_cache = (held[2], head, bone, now + 0.1)
                    return held[2], head, bone
                _aim_cache = (held[2], held[3], held[4], now + 0.1)
                return held[2], held[3], held[4]
        best: tuple[float, float, UObject, tuple[float, float, float], str] | None = None
        nearest_angle = 999.0
        nearest_distance = 0.0
        in_world = 0
        seen: set[str] = set()
        try:
            pawns = unrealsdk.find_all("WillowAIPawn", exact=False)
        except TypeError:
            pawns = unrealsdk.find_all("WillowAIPawn")
        for pawn in pawns:
            key = _obj_key(pawn)
            if key in seen or not _targetable(pawn, player):
                continue
            seen.add(key)
            root = _pawn_root(pawn)
            if root is None:
                continue
            in_world += 1
            if in_world > 64:
                break
            offset = _sub(root, origin)
            distance = _length(offset)
            angle = _angle(forward, offset)
            if angle < nearest_angle:
                nearest_angle = angle
                nearest_distance = distance
            candidate = _consider(pawn, origin, forward, limit, reach)
            if candidate is None:
                continue
            if best is None or candidate[0] < best[0] or (
                abs(candidate[0] - best[0]) <= 0.01 and candidate[1] < best[1]
            ):
                best = candidate
        if best is None:
            _sticky = None
            _aim_cache = None
            _log_once(
                "no-target-detail",
                f"[WPL] 没选中 场上={in_world} 最近夹角={nearest_angle:.0f} 距离={nearest_distance:.0f}",
            )
            return None
        root = _pawn_root(best[2])
        if root is None:
            return best[2], best[3], best[4]
        # 骨骼查询放在已经选中的那一只上，并且此时 _selecting 为真，瞄准函数不会重入。
        head, bone = _weak_point(best[2], root, origin, forward)
        _log_once(f"head:{_class_name(best[2])}:{bone}", f"[WPL] 弱点改为 {bone}")
        _sticky = best[2]
        _aim_cache = (best[2], head, bone, now + 0.1)
        return best[2], head, bone
    finally:
        _selecting = False


def _player_weapon(weapon: Any) -> UObject | None:
    if weapon is None:
        return None
    pc = get_pc()
    local = _get(pc, "Pawn") if pc is not None else None
    for name in ("Owner", "Instigator"):
        pawn = _get(weapon, name)
        if pawn is None:
            continue
        if local is not None and pawn == local:
            return local
        if _class_name(pawn) == "WillowPlayerPawn":
            return pawn
    if local is None:
        return None
    for name in ("Weapon", "EquippedWeapon", "MyWeapon"):
        if _get(local, name) == weapon:
            return local
    return None


def _iter_projectiles() -> list[Any]:
    found: list[Any] = []
    seen: set[str] = set()
    for class_name in ("WillowProjectile",):
        try:
            found = unrealsdk.find_all(class_name, exact=False)
        except TypeError:
            found = unrealsdk.find_all(class_name)
        for proj in found:
            cls = _class_name(proj)
            if "Definition" in cls or "Behavior" in cls:
                continue
            key = _obj_key(proj)
            if key in seen:
                continue
            seen.add(key)
            found.append(proj)
    return found


def _owned_by(proj: Any, pawn: UObject) -> bool | None:
    """True 属于玩家，False 属于别人，None 还看不出主人。"""
    instigator = _try_get(proj, "Instigator")
    if instigator is not _MISSING and instigator is not None:
        return instigator == pawn
    owner = _try_get(proj, "Owner")
    if owner is not _MISSING and owner is not None:
        return owner == pawn or _get(owner, "Owner") == pawn
    for name in ("MyWeapon", "Weapon", "InstigatorWeapon"):
        weapon = _try_get(proj, name)
        if weapon is _MISSING or weapon is None:
            continue
        return _get(weapon, "Owner") == pawn
    return None


def _redirect_projectile(
    proj: Any,
    aim: tuple[float, float, float],
    pawn: UObject,
    before: set[str] | None = None,
) -> bool:
    if proj is None:
        return False
    owned = _owned_by(proj, pawn)
    key = _obj_key(proj)
    if owned is False:
        return False
    if owned is None and (before is None or key in before):
        return False
    if _try_get(proj, "bDeleteMe") is True:
        return False
    origin = _xyz(_try_get(proj, "Location"))
    velocity = _xyz(_try_get(proj, "Velocity"))
    if origin is None or velocity is None:
        return False
    speed = _length(velocity)
    if speed < 10.0:
        return False
    offset = _sub(aim, origin)
    distance = _length(offset)
    if distance < 1.0:
        return False
    new_velocity = _scale(offset, speed / distance)
    if not _write_vector(proj, "Velocity", new_velocity):
        _log_once("velocity-write", "[WPL] 写不进子弹 Velocity")
        return False
    _write_rotation(proj, new_velocity)
    return True


def _rewrite_args(obj: Any, args: WrappedStruct) -> bool:
    shot = _shot
    if shot is None:
        return False
    pawn = shot["pawn"]
    cls = _class_name(obj)
    if "Projectile" in cls and _owned_by(obj, pawn) is False:
        return False
    if "Weapon" in cls and _get(obj, "Owner") != pawn:
        return False
    origin = _xyz(_try_get(obj, "Location"))
    if origin is None:
        origin = _xyz(_try_get(pawn, "Location"))
    if origin is None:
        return False
    offset = _sub(shot["aim"], origin)
    distance = _length(offset)
    if distance < 1.0:
        return False
    direction = _scale(offset, 1.0 / distance)
    wrote = False
    for name in ("Direction", "AimDir", "FireDirection"):
        if _write_vector(args, name, direction):
            wrote = True
    if _write_vector(args, "EndTrace", shot["aim"]):
        wrote = True
    return wrote


def _note_hook(label: str) -> None:
    shot = _shot
    active = shot is not None
    _log_once(f"hook:{label}", f"[WPL] 钩子 {label} 开火窗口={'是' if active else '否'}")
    if shot is not None:
        shot["hooks"].add(label)
        _log_once(f"during:{label}", f"[WPL] 钩子 {label} 赶在 InstantFire 期间")


def _rotator_from_direction(
    direction: tuple[float, float, float],
) -> tuple[int, int]:
    yaw = math.atan2(direction[1], direction[0]) * _RAD_TO_URU
    horiz = math.hypot(direction[0], direction[1])
    pitch = math.atan2(direction[2], horiz) * _RAD_TO_URU
    return int(pitch), int(yaw)


def _aim_rotation(pawn: UObject, pc: UObject | None, aim: tuple[float, float, float]) -> None:
    global _saved_rot
    origin = _view_origin(pawn, pc)
    if origin is None:
        return
    direction = _sub(aim, origin)
    if _length(direction) < 1.0:
        return
    pitch, yaw = _rotator_from_direction(direction)
    saved: list[tuple[Any, int, int, int]] = []
    seen: set[str] = set()
    for obj in (pc, _get(pawn, "Controller")):
        if obj is None:
            continue
        key = _obj_key(obj)
        if key in seen:
            continue
        seen.add(key)
        rot = _try_get(obj, "Rotation")
        if rot is _MISSING:
            continue
        try:
            old_pitch = int(rot.Pitch)
            old_yaw = int(rot.Yaw)
            old_roll = int(rot.Roll)
            obj.Rotation = make_struct(
                "Rotator",
                Pitch=pitch,
                Yaw=yaw,
                Roll=old_roll,
            )
        except Exception as exc:
            _log_once("rot-write", f"[WPL] 写视角失败: {exc}")
            continue
        saved.append((obj, old_pitch, old_yaw, old_roll))
    _saved_rot = saved
    if saved:
        _log_once("aimed", "[WPL] 开火瞬间已把准星对向目标")


def _restore_rotation() -> None:
    global _saved_rot
    saved = _saved_rot
    _saved_rot = None
    if not saved:
        return
    for obj, pitch, yaw, roll in saved:
        try:
            obj.Rotation = make_struct(
                "Rotator",
                Pitch=pitch,
                Yaw=yaw,
                Roll=roll,
            )
        except Exception:
            continue


def _trace(message: str) -> None:
    global _traces
    if not debug_log.value or _traces >= 16:
        return
    _traces += 1
    logging.info(message)


def _open_shot(weapon: UObject) -> None:
    global _shot, _watch
    if not lock_enabled.value:
        return
    pawn = _player_weapon(weapon)
    if pawn is None:
        owner = _get(weapon, "Owner")
        _log_once(
            f"skip:{_class_name(weapon)}:{_class_name(owner)}",
            f"[WPL] 跳过 武器={_class_name(weapon)} "
            f"Owner={_class_name(owner)} "
            f"Instigator={_class_name(_get(weapon, 'Instigator'))}",
        )
        return
    _trace("[WPL] 进入开火")
    pc = _get(pawn, "Controller")
    if pc is None:
        pc = get_pc()
    selected = _select(pawn, pc)
    if selected is None:
        _trace("[WPL] 视野内没有可用弱点")
        _log_once("no-target", "[WPL] 射击 视野内没有可用弱点")
        return
    target, aim, bone = selected
    _shot = {
        "pawn": pawn,
        "aim": aim,
        "bone": bone,
        "target": _obj_key(target),
        "before": {_obj_key(proj) for proj in _iter_projectiles()},
        "hooks": set(),
        "redirected": set(),
    }
    _watch = {
        "before": set(_shot["before"]),
        "aim": aim,
        "pawn": pawn,
        "frames": 12,
    }
    _aim_rotation(pawn, pc, aim)
    _trace(
        f"[WPL] 选中 {_class_name(target)} 骨骼={bone} "
        f"aim=({aim[0]:.0f},{aim[1]:.0f},{aim[2]:.0f})"
    )


def _close_shot() -> None:
    global _shot
    shot = _shot
    _shot = None
    _restore_rotation()
    _trace("[WPL] 开火结束")
    if shot is None:
        return
    added = _claim_new_projectiles(shot["aim"], shot["pawn"], shot["before"])
    if _watch is not None:
        _watch["before"] = set(shot["before"])
    if added == 0 and not _pending:
        _log_once("no-redirect", "[WPL] 开火函数里没有新子弹，已改用临时准星")
    hooks = ",".join(sorted(shot["hooks"])) or "无"
    _remember_shot(
        f"[WPL] 射击 目标={shot['target']} 骨骼={shot['bone']} "
        f"钩子={hooks} 改写={added} 等待={len(_pending)}"
    )
    if added:
        _hud_once("redirected", "弱点锁定", "子弹已改向目标")


def _remember_shot(message: str) -> None:
    global _shot_logs
    if not debug_log.value or _shot_logs >= _MAX_SHOT_LOGS:
        return
    _shot_logs += 1
    logging.info(message)


def _on_damage(label: str, obj: UObject, args: WrappedStruct) -> None:
    global _hit_logs
    if not debug_log.value:
        return
    cls = _class_name(obj)
    _log_once(f"dmg-args:{label}", f"[WPL] {label} 参数: {_describe(args)}")
    if "Player" in cls or _hit_logs >= _MAX_HIT_LOGS:
        return
    if not _damage_from_player(args):
        return
    _hit_logs += 1
    logging.info(f"[WPL] 命中 {label} 对象={cls} {_describe(args)}")


def _damage_from_player(args: WrappedStruct) -> bool:
    pc = get_pc()
    pawn = _get(pc, "Pawn") if pc is not None else None
    saw_field = False
    for name in ("EventInstigator", "InstigatedBy", "Instigator", "DamageCauser"):
        value = _try_get(args, name)
        if value is _MISSING:
            continue
        saw_field = True
        if value is None:
            continue
        if value == pc or value == pawn or _get(value, "Pawn") == pawn:
            return True
        if _get(value, "Instigator") == pawn or _get(value, "Owner") == pawn:
            return True
    return not saw_field


@hook("WillowGame.WillowWeapon:InstantFire", Type.PRE)
def on_instant_fire_pre(
    obj: UObject,
    _args: WrappedStruct,
    _ret: Any,
    _func: BoundFunction,
) -> None:
    global _depth
    try:
        if _depth == 0:
            _open_shot(obj)
    except Exception as exc:
        logging.error(f"[WPL] 开火前失败: {exc}")
    _depth += 1


@hook("WillowGame.WillowWeapon:InstantFire", Type.POST)
def on_instant_fire_post(
    _obj: UObject,
    _args: WrappedStruct,
    _ret: Any,
    _func: BoundFunction,
) -> None:
    global _depth
    if _depth > 0:
        _depth -= 1
    if _depth != 0:
        return
    try:
        _close_shot()
    except Exception as exc:
        global _shot
        _shot = None
        logging.error(f"[WPL] 开火后失败: {exc}")


def _make_arg_hook(label: str):
    def _hook(
        obj: UObject,
        args: WrappedStruct,
        _ret: Any,
        _func: BoundFunction,
    ) -> None:
        try:
            _note_hook(label)
            if _rewrite_args(obj, args):
                _log_once(f"args:{label}", f"[WPL] 已改 {label} 的方向参数")
        except Exception as exc:
            _log_once(f"arg-err:{label}", f"[WPL] {label} 改参数失败: {exc}")

    return _hook


def _make_velocity_hook(label: str):
    def _hook(
        obj: UObject,
        _args: WrappedStruct,
        _ret: Any,
        _func: BoundFunction,
    ) -> None:
        try:
            _note_hook(label)
            shot = _shot
            if shot is None:
                return
            if _redirect_projectile(obj, shot["aim"], shot["pawn"], shot["before"]):
                shot["redirected"].add(_obj_key(obj))
                _log_once(f"vel:{label}", f"[WPL] 已在 {label} 改写速度")
        except Exception as exc:
            _log_once(f"vel-err:{label}", f"[WPL] {label} 改速度失败: {exc}")

    return _hook


def _make_damage_hook(label: str):
    def _hook(
        obj: UObject,
        args: WrappedStruct,
        _ret: Any,
        _func: BoundFunction,
    ) -> None:
        try:
            _on_damage(label, obj, args)
        except Exception as exc:
            _log_once(f"dmg-err:{label}", f"[WPL] {label} 记录命中失败: {exc}")

    return _hook


def _bind(path: str, hook_type: Type, callback: Any) -> None:
    ident = f"wpl:{getattr(hook_type, 'name', hook_type)}:{path}"
    try:
        add_hook(path, hook_type, ident, callback)
    except Exception as exc:
        logging.info(f"[WPL] 未挂上 {path} {hook_type}: {exc}")
        return
    _manual.append((path, hook_type, ident))
    logging.info(f"[WPL] 已挂上 {path}")


def _pawn_from_aimer(obj: Any) -> UObject | None:
    if obj is None:
        return None
    cls = _class_name(obj)
    if "Weapon" in cls:
        return _player_weapon(obj)
    local = None
    pc = get_pc()
    if pc is not None:
        local = _get(pc, "Pawn")
        if obj == pc:
            return local
    if local is not None and (obj == local or obj == _get(local, "Controller")):
        return local
    return None


def _aim_for_pawn(pawn: UObject) -> tuple[tuple[float, float, float], str] | None:
    if _shot is not None and _shot.get("pawn") == pawn:
        return _shot["aim"], _shot["bone"]
    if _watch is not None and _watch.get("pawn") == pawn:
        return _watch["aim"], _watch.get("bone", "UpperBody")
    pc = _get(pawn, "Controller")
    if pc is None:
        pc = get_pc()
    selected = _select(pawn, pc)
    if selected is None:
        return None
    return selected[1], selected[2]


def _rotator_toward(origin: tuple[float, float, float], aim: tuple[float, float, float]) -> Any | None:
    direction = _sub(aim, origin)
    if _length(direction) < 1.0:
        return None
    pitch, yaw = _rotator_from_direction(direction)
    try:
        return make_struct("Rotator", Pitch=pitch, Yaw=yaw, Roll=0)
    except Exception as exc:
        _log_once("rot-make", f"[WPL] 生成视角失败: {exc}")
        return None


def _make_aim_hook(label: str):
    def _hook(
        obj: UObject,
        args: WrappedStruct,
        _ret: Any,
        _func: BoundFunction,
    ) -> tuple[type[Block], Any] | None:
        if not lock_enabled.value or _selecting:
            return None
        try:
            pawn = _pawn_from_aimer(obj)
            if pawn is None:
                return None
            _log_once(f"call:{label}", f"[WPL] 调用了 {label}")
            aimed = _aim_for_pawn(pawn)
            if aimed is None:
                return None
            aim, bone = aimed
            origin = _xyz(_try_get(args, "StartFireLoc"))
            if origin is None:
                origin = _xyz(_try_get(args, "StartTrace"))
            if origin is None:
                origin = _view_origin(pawn, _get(pawn, "Controller") or get_pc())
            if origin is None:
                return None
            rotator = _rotator_toward(origin, aim)
            if rotator is None:
                return None
            _log_once(f"lock:{label}", f"[WPL] {label} 改为锁定 {bone}")
            return (Block, rotator)
        except Exception as exc:
            _log_once(f"aim-err:{label}", f"[WPL] {label} 失败: {exc}")
            return None

    return _hook


def _make_trace_hook(label: str):
    def _hook(
        obj: UObject,
        args: WrappedStruct,
        _ret: Any,
        _func: BoundFunction,
    ) -> None:
        if not lock_enabled.value or _selecting:
            return
        try:
            pawn = _pawn_from_aimer(obj)
            if pawn is None:
                return
            _log_once(f"call:{label}", f"[WPL] 调用了 {label}")
            aimed = _aim_for_pawn(pawn)
            if aimed is None:
                return
            aim, bone = aimed
            start = _xyz(_try_get(args, "StartTrace"))
            if start is None:
                start = _view_origin(pawn, _get(pawn, "Controller") or get_pc())
            if start is None:
                return
            direction = _sub(aim, start)
            distance = _length(direction)
            if distance < 1.0:
                return
            end = _add(aim, _scale(direction, 8000.0 / distance))
            if _write_vector(args, "EndTrace", end):
                _log_once(f"lock:{label}", f"[WPL] {label} 终点改为 {bone}")
        except Exception as exc:
            _log_once(f"trace-err:{label}", f"[WPL] {label} 失败: {exc}")

    return _hook


def _make_init_hook(label: str):
    def _hook(
        obj: UObject,
        args: WrappedStruct,
        _ret: Any,
        _func: BoundFunction,
    ) -> None:
        try:
            _log_once(f"call:{label}", f"[WPL] 调用了 {label} 子弹={_class_name(obj)}")
            if not lock_enabled.value or _selecting:
                return
            pawn = None
            if _shot is not None:
                pawn = _shot["pawn"]
                aim = _shot["aim"]
            elif _watch is not None:
                pawn = _watch["pawn"]
                aim = _watch["aim"]
            else:
                return
            if _owned_by(obj, pawn) is False:
                return
            origin = _xyz(_try_get(obj, "Location"))
            if origin is None:
                origin = _view_origin(pawn, _get(pawn, "Controller") or get_pc())
            if origin is None:
                return
            direction = _sub(aim, origin)
            distance = _length(direction)
            if distance < 1.0:
                return
            unit = _scale(direction, 1.0 / distance)
            if _write_vector(args, "Direction", unit):
                _log_once(f"lock:{label}", f"[WPL] {label} 已改子弹方向")
        except Exception as exc:
            _log_once(f"init-err:{label}", f"[WPL] {label} 失败: {exc}")

    return _hook


def _add(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return (left[0] + right[0], left[1] + right[1], left[2] + right[2])


def _bind_candidates() -> None:
    for path in (
        "Engine.Weapon:GetAdjustedAim",
        "WillowGame.WillowWeapon:GetAdjustedAim",
        "Engine.Controller:GetAdjustedAimFor",
        "Engine.PlayerController:GetAdjustedAimFor",
        "WillowGame.WillowPlayerController:GetAdjustedAimFor",
    ):
        _bind(path, Type.PRE, _make_aim_hook(path))
    for path in (
        "Engine.Weapon:CalcWeaponFire",
        "WillowGame.WillowWeapon:CalcWeaponFire",
        "Engine.Weapon:FireAmmunition",
        "WillowGame.WillowWeapon:FireAmmunition",
    ):
        _bind(path, Type.PRE, _make_trace_hook(path))
    for path in (
        "Engine.Projectile:Init",
        "WillowGame.WillowProjectile:Init",
    ):
        _bind(path, Type.PRE, _make_init_hook(path))


def _unbind_candidates() -> None:
    while _manual:
        path, hook_type, ident = _manual.pop()
        try:
            remove_hook(path, hook_type, ident)
        except TypeError:
            try:
                remove_hook(path, ident)
            except Exception as exc:
                logging.error(f"[WPL] 卸下 {path} 失败: {exc}")
        except Exception as exc:
            logging.error(f"[WPL] 卸下 {path} 失败: {exc}")


def _claim_new_projectiles(aim: tuple[float, float, float], pawn: UObject, before: set[str]) -> int:
    added = 0
    for proj in _iter_projectiles():
        key = _obj_key(proj)
        if key in before:
            continue
        if _owned_by(proj, pawn) is False:
            before.add(key)
            continue
        if _redirect_projectile(proj, aim, pawn, set()):
            before.add(key)
            added += 1
            continue
        if len(_pending) < 24:
            _pending.append({"proj": proj, "aim": aim, "pawn": pawn, "frames": 10})
            before.add(key)
    return added


def _flush_pending() -> None:
    if not _pending:
        return
    alive: list[dict[str, Any]] = []
    for item in _pending:
        item["frames"] = int(item["frames"]) - 1
        try:
            moved = _redirect_projectile(item["proj"], item["aim"], item["pawn"], set())
        except Exception:
            moved = False
        if moved:
            _trace(f"[WPL] 延迟改写 {_class_name(item['proj'])}")
            continue
        if item["frames"] > 0:
            alive.append(item)
    _pending[:] = alive


@hook("WillowGame.WillowPlayerController:PlayerTick", Type.POST)
def on_player_tick(
    obj: UObject,
    _args: WrappedStruct,
    _ret: Any,
    _func: BoundFunction,
) -> None:
    global _watch
    if not _pending and _watch is None:
        return
    pc = get_pc()
    if pc is not None and obj != pc:
        return
    try:
        if _watch is not None:
            added = _claim_new_projectiles(_watch["aim"], _watch["pawn"], _watch["before"])
            _watch["frames"] = int(_watch["frames"]) - 1
            if added:
                _trace(f"[WPL] 延迟改写 {added} 发子弹")
            if _watch["frames"] <= 0:
                _watch = None
        _flush_pending()
    except Exception as exc:
        _pending.clear()
        _watch = None
        logging.error(f"[WPL] 延迟改写失败: {exc}")


def on_enable() -> None:
    global _shot_logs, _hit_logs, _depth, _shot, _traces, _watch, _saved_rot
    global _selecting, _aim_cache, _sticky
    _logged.clear()
    _crit_bones.clear()
    _shaped.clear()
    _hud_sent.clear()
    _shot_logs = 0
    _hit_logs = 0
    _traces = 0
    _depth = 0
    _shot = None
    _watch = None
    _saved_rot = None
    _sticky = None
    _selecting = False
    _aim_cache = None
    _pending.clear()
    if not _manual:
        _bind_candidates()
    logging.info("[WPL] 弱点锁定已启用")


def on_disable() -> None:
    global _shot, _depth, _sticky, _watch, _saved_rot, _selecting, _aim_cache
    _restore_rotation()
    _unbind_candidates()
    _shot = None
    _depth = 0
    _sticky = None
    _watch = None
    _saved_rot = None
    _selecting = False
    _aim_cache = None
    _pending.clear()
    logging.info("[WPL] 弱点锁定已关闭")


build_mod(
    on_enable=on_enable,
    on_disable=on_disable,
    supported_games=Game.BL2,
    coop_support=CoopSupport.HostOnly,
)
