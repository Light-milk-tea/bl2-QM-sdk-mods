from __future__ import annotations

from dataclasses import dataclass

from mods_base import ButtonOption, CoopSupport, Game, SliderOption, build_mod, get_pc
from ui_utils import show_hud_message
from unrealsdk import logging

TITLE = "调整货币"


@dataclass
class Currency:
    index: int
    option_id: str
    label: str
    maximum: int
    slider: SliderOption


def _replication_info():
    pc = get_pc()
    if pc is None:
        return None
    try:
        return pc.PlayerReplicationInfo
    except Exception:
        return None


def _show_missing() -> None:
    show_hud_message(TITLE, "现在不在游戏里")


def _clamp(amount: int, maximum: int) -> int:
    if amount < 0:
        return 0
    if amount > maximum:
        return maximum
    return amount


def _read_amount(info, currency: Currency) -> int:
    return int(info.GetCurrencyOnHand(currency.index))


def _slider_amount(currency: Currency, amount: int) -> int:
    step = int(currency.slider.step)
    snapped = (amount // step) * step
    high = int(currency.slider.max_value)
    low = int(currency.slider.min_value)
    if snapped > high:
        return high
    if snapped < low:
        return low
    return snapped


def _write_amount(info, currency: Currency, amount: int) -> int:
    target = _clamp(amount, currency.maximum)
    info.SetCurrencyOnHand(currency.index, target)
    return _read_amount(info, currency)


def _format_amounts(pairs: list[tuple[str, int]]) -> str:
    return "，".join(f"{label} {amount:,}" for label, amount in pairs)


def _set_status(text: str) -> None:
    status_line.display_name = text
    status_line.description = text
    status_line.description_title = text


def _reload_menu() -> None:
    try:
        from willow2_mod_menu.options_menu import latest_list

        the_list = latest_list()
        if the_list is None:
            logging.error("[set_currency] 刷新菜单失败: 菜单列表不存在")
            return
        the_list.Refresh()
    except Exception as exc:
        logging.error(f"[set_currency] 刷新菜单失败: {exc}")


def _on_read(_option: ButtonOption) -> None:
    info = _replication_info()
    if info is None:
        _set_status("现在不在游戏里")
        _reload_menu()
        _show_missing()
        return
    pairs: list[tuple[str, int]] = []
    try:
        for currency in CURRENCIES:
            amount = _read_amount(info, currency)
            currency.slider.value = _slider_amount(currency, amount)
            currency.slider.display_name = f"{currency.label} {amount:,}"
            currency.slider.description_title = currency.slider.display_name
            pairs.append((currency.label, amount))
    except Exception as exc:
        logging.error(f"[set_currency] 读取失败: {exc}")
        _set_status("读取失败")
        _reload_menu()
        show_hud_message(TITLE, "读取失败")
        return
    text = _format_amounts(pairs)
    logging.info(f"[set_currency] 读取 {text}")
    _set_status(text)
    _reload_menu()
    show_hud_message(TITLE, text)


def _on_write_sliders(_option: ButtonOption) -> None:
    info = _replication_info()
    if info is None:
        _show_missing()
        return
    pairs: list[tuple[str, int]] = []
    try:
        for currency in CURRENCIES:
            actual = _write_amount(info, currency, int(currency.slider.value))
            pairs.append((currency.label, actual))
    except Exception as exc:
        logging.error(f"[set_currency] 写入失败: {exc}")
        show_hud_message(TITLE, "写入失败")
        return
    logging.info("[set_currency] " + _format_amounts(pairs))
    show_hud_message(TITLE, _format_amounts(pairs))


def _on_write_maximum(_option: ButtonOption) -> None:
    info = _replication_info()
    if info is None:
        _show_missing()
        return
    pairs: list[tuple[str, int]] = []
    try:
        for currency in CURRENCIES:
            currency.slider.value = int(currency.slider.max_value)
            actual = _write_amount(info, currency, currency.maximum)
            pairs.append((currency.label, actual))
    except Exception as exc:
        logging.error(f"[set_currency] 写入上限失败: {exc}")
        show_hud_message(TITLE, "写入失败")
        return
    logging.info("[set_currency] 上限 " + _format_amounts(pairs))
    show_hud_message(TITLE, _format_amounts(pairs))


def _write_one(currency: Currency) -> None:
    info = _replication_info()
    if info is None:
        _show_missing()
        return
    try:
        actual = _write_amount(info, currency, int(currency.slider.value))
    except Exception as exc:
        logging.error(f"[set_currency] 写入{currency.label}失败: {exc}")
        show_hud_message(TITLE, f"{currency.label}写入失败")
        return
    logging.info(f"[set_currency] {currency.label} {actual}")
    show_hud_message(TITLE, f"{currency.label} {actual:,}")


money = SliderOption(
    identifier="Money",
    value=99_000_000,
    min_value=0,
    max_value=99_000_000,
    step=1_000,
    is_integer=True,
    display_name="金钱",
    description="左右调整，每次 1000，滑条最高 9900 万。要 99999999 用「四种都设为上限」。",
)
eridium = SliderOption(
    identifier="Eridium",
    value=500,
    min_value=0,
    max_value=500,
    step=1,
    is_integer=True,
    display_name="铱矿",
    description="游戏上限是 500。",
)
seraph = SliderOption(
    identifier="Seraph Crystals",
    value=999,
    min_value=0,
    max_value=999,
    step=1,
    is_integer=True,
    display_name="炽天使水晶",
    description="游戏上限是 999。",
)
torgue = SliderOption(
    identifier="Torgue Tokens",
    value=999,
    min_value=0,
    max_value=999,
    step=1,
    is_integer=True,
    display_name="托格代币",
    description="游戏上限是 999。",
)

CURRENCIES = (
    Currency(0, "Money", "金钱", 99_999_999, money),
    Currency(1, "Eridium", "铱矿", 500, eridium),
    Currency(2, "Seraph Crystals", "炽天使水晶", 999, seraph),
    Currency(4, "Torgue Tokens", "托格代币", 999, torgue),
)

status_line = ButtonOption(
    identifier="Status",
    display_name="当前数量还没读取",
    description="点下面的「读取当前数量」。读到的数会显示在这一行，滑条也会跟着变。",
)

read_button = ButtonOption(
    identifier="Read Current",
    display_name="读取当前数量",
    description="把这个角色身上的四种数量填进下面的滑条。",
    on_press=_on_read,
)
write_button = ButtonOption(
    identifier="Write Sliders",
    display_name="按滑条写入",
    description="把四个滑条上的数量写入当前角色。",
    on_press=_on_write_sliders,
)
maximum_button = ButtonOption(
    identifier="Write Maximum",
    display_name="四种都设为上限",
    description="金钱 99999999，铱矿 500，炽天使水晶 999，托格代币 999。",
    on_press=_on_write_maximum,
)


def _make_write_button(currency: Currency) -> ButtonOption:
    def on_press(_option: ButtonOption) -> None:
        _write_one(currency)

    return ButtonOption(
        identifier=f"Write {currency.option_id}",
        display_name=f"只写入{currency.label}",
        description=f"只改{currency.label}，另外三种不动。",
        on_press=on_press,
    )


write_buttons = tuple(_make_write_button(currency) for currency in CURRENCIES)

build_mod(
    options=[
        status_line,
        read_button,
        write_button,
        maximum_button,
        money,
        write_buttons[0],
        eridium,
        write_buttons[1],
        seraph,
        write_buttons[2],
        torgue,
        write_buttons[3],
    ],
    supported_games=Game.BL2,
    coop_support=CoopSupport.ClientSide,
)
