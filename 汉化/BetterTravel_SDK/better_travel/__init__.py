from mods_base import build_mod

from .spawns import on_load_save, on_save_game, on_spawn_tp, on_spawn_travel
from .travel import on_place_marker, open_fast_travel

mod = build_mod(
    keybinds=[open_fast_travel],
    hooks=[on_save_game, on_load_save, on_spawn_travel, on_spawn_tp, on_place_marker],
)
