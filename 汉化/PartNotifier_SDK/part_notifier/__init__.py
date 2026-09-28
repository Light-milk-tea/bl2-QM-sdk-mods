from __future__ import annotations

import gzip
import json
from collections import Counter
from pathlib import Path

from mods_base import BoolOption, Game, NestedOption, SliderOption, build_mod, hook, open_in_mod_dir
from unrealsdk import find_object, logging
from unrealsdk.hooks import Block, prevent_hooking_direct_calls
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

from .part_namer import legacy_get_part_name

_EMPHASIS = "#FFDEAD"
_SHOW = "是否在物品说明里显示这个部件。"
_EXTRA = "平时不用的槽里如果有部件，是否也显示出来。"
_PARTS_PATH = Path(__file__).resolve().parent / "part_names.json.gz"

_TPS_TYPE_REPLACEMENTS = {"Relic": "Oz Kit"}
_LEGACY_TPS_REPLACEMENTS = {
    **_TPS_TYPE_REPLACEMENTS,
    "Bandit": "Scav",
    "Bouncing Bonny": "Bouncing Bazza",
}


def _load_part_names() -> dict[str, dict[str, str]]:
    try:
        with open_in_mod_dir(_PARTS_PATH, binary=True) as raw:
            with gzip.GzipFile(fileobj=raw) as compressed:
                loaded = json.load(compressed)
    except (OSError, json.JSONDecodeError, EOFError):
        logging.error("[装备部件提示] 部件名字库读取失败")
        return {}
    return loaded if isinstance(loaded, dict) else {}


_PART_NAMES = _load_part_names()


def _replace(text: str, replacements: dict[str, str]) -> str:
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text


def _movie_player() -> UObject | None:
    try:
        return find_object("GFxMoviePlayer", "GFxUI.Default__GFxMoviePlayer")
    except Exception:
        return None


def _part_name(part: UObject, show_slot: bool, show_type: bool) -> str:
    obj_name = part.PathName(part)
    info = _PART_NAMES.get(obj_name)
    if info is None:
        output = legacy_get_part_name(part, show_slot, show_type)
        if Game.get_current() == Game.TPS:
            output = _replace(output, _LEGACY_TPS_REPLACEMENTS)
        return output

    name = info["name"]
    try:
        name = info["game_overrides"][Game.get_current().name]
    except KeyError:
        pass
    player = _movie_player()
    if player is not None:
        name = player.ResolveDataStoreMarkup(name)
    item_type = info["type"]
    if Game.get_current() == Game.TPS:
        item_type = _replace(item_type, _TPS_TYPE_REPLACEMENTS)
    output = name
    if show_type:
        output += " " + item_type
    if show_slot:
        output += " " + info["slot"]
    return output


def _toggle(identifier: str, display: str, enabled: bool, description: str = _SHOW) -> BoolOption:
    return BoolOption(identifier, enabled, "开", "关", display_name=display, description=description)


def _slots_text(item: UObject, slots: tuple[str, ...], title: str) -> str:
    counts: Counter[UObject] = Counter()
    data = item.DefinitionData
    for slot in slots:
        part = getattr(data, slot, None)
        if part is not None:
            counts[part] += 1
    if not counts:
        return ""
    show_slot = slot_option.value
    show_type = type_option.value
    pieces: list[str] = []
    for part, count in counts.items():
        piece = _part_name(part, show_slot, show_type)
        if count > 1:
            piece += f" x{count}"
        pieces.append(piece)
    return f"{title}: <font color='{_EMPHASIS}'>{', '.join(pieces)}</font>\n"


type_option = _toggle("include_item_type", "显示物品类型", False, "部件名字后面要不要带上它原本属于哪种装备。")
slot_option = _toggle("include_part_slots", "显示部件槽位", False, "部件名字后面要不要带上它原本装在哪个槽。")
font_option = SliderOption(
    "font_size",
    14,
    8,
    24,
    1,
    display_name="字体大小",
    description="部件文字的字号。说明被截断时就把它调小。",
)
remove_option = _toggle("remove_descriptions", "去掉原说明", False, "是否去掉物品卡原来的趣味说明，给部件文字腾出位置。")


def _group(
    identifier: str,
    display: str,
    item_class: str,
    parts: list[tuple[BoolOption, tuple[str, ...]]],
    *,
    hidden: bool = False,
) -> tuple[str, NestedOption, list[tuple[BoolOption, tuple[str, ...]]]]:
    nested = NestedOption(
        identifier,
        [option for option, _slots in parts],
        display_name=display,
        description=f"选择要显示哪些{display}部件。",
        is_hidden=hidden,
    )
    return item_class, nested, parts


_on_tps = Game.get_current() == Game.TPS
_groups = [
    _group(
        "weapons",
        "武器",
        "WillowWeapon",
        [
            (_toggle("weapon_accessory", "配件", True), ("Accessory1PartDefinition",)),
            (_toggle("weapon_accessory_2", "第二配件", False), ("Accessory2PartDefinition",)),
            (_toggle("weapon_barrel", "枪管", True), ("BarrelPartDefinition",)),
            (_toggle("weapon_body", "本体", False), ("BodyPartDefinition",)),
            (_toggle("weapon_element", "元素", False), ("ElementalPartDefinition",)),
            (_toggle("weapon_grip", "握把", True), ("GripPartDefinition",)),
            (_toggle("weapon_material", "材质", False), ("MaterialPartDefinition",)),
            (_toggle("weapon_sight", "瞄具", True), ("SightPartDefinition",)),
            (_toggle("weapon_stock", "枪托", True), ("StockPartDefinition",)),
            (_toggle("weapon_definition", "定义", False), ("WeaponTypeDefinition",)),
        ],
    ),
    _group(
        "shields",
        "护盾",
        "WillowShield",
        [
            (_toggle("shield_accessory", "配件", False), ("DeltaItemPartDefinition",)),
            (_toggle("shield_battery", "电池", True), ("BetaItemPartDefinition",)),
            (_toggle("shield_body", "本体", True), ("AlphaItemPartDefinition",)),
            (_toggle("shield_capacitor", "电容", True), ("GammaItemPartDefinition",)),
            (_toggle("shield_material", "材质", False), ("MaterialItemPartDefinition",)),
            (_toggle("shield_definition", "定义", False), ("ItemDefinition",)),
            (
                _toggle("shield_extras", "额外槽位", True, _EXTRA),
                (
                    "EpsilonItemPartDefinition",
                    "ZetaItemPartDefinition",
                    "EtaItemPartDefinition",
                    "ThetaItemPartDefinition",
                ),
            ),
        ],
    ),
    _group(
        "grenades",
        "手雷",
        "WillowGrenadeMod",
        [
            (_toggle("grenade_accessory", "配件", False), ("DeltaItemPartDefinition",)),
            (_toggle("grenade_radius", "爆炸范围", True), ("ZetaItemPartDefinition",)),
            (_toggle("grenade_children", "子雷数量", True), ("EtaItemPartDefinition",)),
            (_toggle("grenade_damage", "伤害", False), ("EpsilonItemPartDefinition",)),
            (_toggle("grenade_delivery", "投掷方式", True), ("BetaItemPartDefinition",)),
            (_toggle("grenade_material", "材质", False), ("MaterialItemPartDefinition",)),
            (_toggle("grenade_payload", "弹头", False), ("AlphaItemPartDefinition",)),
            (_toggle("grenade_status", "异常伤害", False), ("ThetaItemPartDefinition",)),
            (_toggle("grenade_trigger", "引爆", True), ("GammaItemPartDefinition",)),
            (_toggle("grenade_definition", "定义", False), ("ItemDefinition",)),
        ],
    ),
    _group(
        "class_mods",
        "职业模组",
        "WillowClassMod",
        [
            (_toggle("com_specialization", "专精", True), ("AlphaItemPartDefinition",)),
            (_toggle("com_primary", "主属性", True), ("BetaItemPartDefinition",)),
            (_toggle("com_secondary", "副属性", True), ("GammaItemPartDefinition",)),
            (_toggle("com_penalty", "惩罚", True), ("MaterialItemPartDefinition",)),
            (_toggle("com_definition", "定义", False), ("ItemDefinition",)),
            (
                _toggle("com_extras", "额外槽位", True, _EXTRA),
                (
                    "DeltaItemPartDefinition",
                    "EpsilonItemPartDefinition",
                    "ZetaItemPartDefinition",
                    "EtaItemPartDefinition",
                    "ThetaItemPartDefinition",
                ),
            ),
        ],
    ),
    _group(
        "relics",
        "圣物",
        "WillowArtifact",
        [
            (_toggle("relic_body", "本体", False), ("EtaItemPartDefinition",)),
            (_toggle("relic_upgrade", "升级", True), ("ThetaItemPartDefinition",)),
            (_toggle("relic_alpha", "阿尔法", False), ("AlphaItemPartDefinition",)),
            (_toggle("relic_beta", "贝塔", False), ("BetaItemPartDefinition",)),
            (_toggle("relic_gamma", "伽马", False), ("GammaItemPartDefinition",)),
            (_toggle("relic_delta", "德尔塔", False), ("DeltaItemPartDefinition",)),
            (_toggle("relic_epsilon", "艾普西龙", False), ("EpsilonItemPartDefinition",)),
            (_toggle("relic_zeta", "泽塔", False), ("ZetaItemPartDefinition",)),
            (_toggle("relic_definition", "定义", False), ("ItemDefinition",)),
            (_toggle("relic_extras", "额外槽位", True, _EXTRA), ("MaterialItemPartDefinition",)),
        ],
        hidden=_on_tps,
    ),
    _group(
        "oz_kits",
        "氧气套件",
        "WillowArtifact",
        [
            (_toggle("oz_body", "本体", False), ("EtaItemPartDefinition",)),
            (_toggle("oz_upgrade", "升级", True), ("ThetaItemPartDefinition",)),
            (_toggle("oz_alpha", "阿尔法", False), ("AlphaItemPartDefinition",)),
            (_toggle("oz_beta", "贝塔", False), ("BetaItemPartDefinition",)),
            (_toggle("oz_gamma", "伽马", False), ("GammaItemPartDefinition",)),
            (_toggle("oz_delta", "德尔塔", False), ("DeltaItemPartDefinition",)),
            (_toggle("oz_epsilon", "艾普西龙", False), ("EpsilonItemPartDefinition",)),
            (_toggle("oz_zeta", "泽塔", False), ("ZetaItemPartDefinition",)),
            (_toggle("oz_definition", "定义", False), ("ItemDefinition",)),
            (_toggle("oz_extras", "额外槽位", True, _EXTRA), ("MaterialItemPartDefinition",)),
        ],
        hidden=not _on_tps,
    ),
]


@hook("WillowGame.ItemCardGFxObject:SetFunStats")
def _block_fun_stats(obj: UObject, args: WrappedStruct, ret: object, func: BoundFunction) -> Block:
    _block_fun_stats.disable()
    return Block


@hook("WillowGame.ItemCardGFxObject:SetItemCardEx")
def on_item_card(obj: UObject, args: WrappedStruct, ret: object, func: BoundFunction) -> None:
    raw_item = getattr(args, "InventoryItem", None)
    item = getattr(raw_item, "ObjectPointer", raw_item)
    if item is None:
        return
    class_name = str(item.Class.Name)
    part_text = ""
    for item_class, _nested, parts in _groups:
        if item_class != class_name:
            continue
        for option, slots in parts:
            if option.value:
                part_text += _slots_text(item, slots, option.display_name)
        break
    else:
        return
    if not part_text:
        return

    text = "" if remove_option.value else (item.GenerateFunStatsText() or "")
    text += f"<font size=\"{int(font_option.value)}\" color=\"#FFFFFF\">{part_text}</font>"
    _block_fun_stats.enable()
    with prevent_hooking_direct_calls():
        obj.SetFunStats(text)


mod = build_mod(
    options=[
        type_option,
        slot_option,
        font_option,
        remove_option,
        *[nested for _item_class, nested, _parts in _groups],
    ],
    hooks=[on_item_card],
)
