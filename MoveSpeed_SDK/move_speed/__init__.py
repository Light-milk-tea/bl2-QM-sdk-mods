from __future__ import annotations

# 菜单档位、登记模组、拿当前玩家、在游戏函数上挂钩。
# Type.POST 表示游戏原来的函数先跑完，再跑我们的代码。
# 最后一行只是类型标注，不参与改速度。
from mods_base import SpinnerOption, build_mod, get_pc, hook
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

# 记住每个角色的原速，以及上一帧我们写进去的速度。
# 用内存地址当钥匙，因为同一个角色每帧在 Python 里可能是一个新包装。
_tracked: dict[int, tuple[float, float]] = {}


def _address(obj: UObject) -> int:
    return obj._get_address()


# 读出当前地面速度，乘上菜单里的倍率，再写回去。
# 第一次见到角色时记下原速；速度还小于 1 就先不记，避免把加载时的 0 当成原速。
# 如果当前速度还是我们上次写的，继续用原速来乘，避免 2 倍变成 4 倍、8 倍。
# 如果游戏自己改过速度（比如冲刺），那个新数字就当成新的原速。
def _apply(pawn: UObject) -> None:
    try:
        current = float(pawn.GroundSpeed)
    except Exception:
        return

    address = _address(pawn)
    multiplier = float(speed.value)
    known = _tracked.get(address)
    if known is None:
        if current < 1.0:
            return
        base = current
    elif abs(current - known[1]) > 1.0 and current >= 1.0:
        base = current
    else:
        base = known[0]

    target = base * multiplier
    if abs(current - target) > 0.05:
        pawn.GroundSpeed = target
    _tracked[address] = (base, target)


# 关掉模组时，如果现在的速度还是我们写的，就改回原速。
def _restore(pawn: UObject) -> None:
    known = _tracked.pop(_address(pawn), None)
    if known is None:
        return
    try:
        current = float(pawn.GroundSpeed)
    except Exception:
        return
    if abs(current - known[1]) <= 1.0:
        pawn.GroundSpeed = known[0]


# 你的角色每一帧都会走到这里。obj 是你的控制器，Pawn 是你正在操控的人。
# 还没有角色，或者当前开的是车，就什么都不改。
@hook("WillowGame.WillowPlayerController:PlayerTick", Type.POST)
def on_player_tick(
    obj: UObject,
    args: WrappedStruct,
    ret: object,
    func: BoundFunction,
) -> None:
    pawn = getattr(obj, "Pawn", None)
    if pawn is None:
        return
    class_name = str(getattr(getattr(pawn, "Class", None), "Name", ""))
    if "Vehicle" in class_name:
        return
    _apply(pawn)


# 关掉模组时，先把当前角色的速度改回去，再清空记录。
def on_disable() -> None:
    pc = get_pc()
    pawn = getattr(pc, "Pawn", None) if pc is not None else None
    if pawn is not None:
        _restore(pawn)
    _tracked.clear()


# 菜单滑条留不住小数，所以做成 "0.0" 到 "5.0"、每次 0.1 的文字档。
# 默认 2.0。最后一行把它登记进 MODS 菜单。
_SPEED_CHOICES = [f"{tenths / 10:.1f}" for tenths in range(51)]

speed = SpinnerOption(
    "MoveSpeedMultiplier",
    "2.0",
    _SPEED_CHOICES,
    display_name="移速倍率",
    description="从 0.0 到 5.0，每次 0.1。1.0 是原来的速度。只改你自己正在操控的角色。",
)

mod = build_mod(hooks=[on_player_tick], options=[speed], on_disable=on_disable)
