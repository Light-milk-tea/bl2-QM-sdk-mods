from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from mods_base import CoopSupport, Game, build_mod, hook
from unrealsdk import find_all, find_class, find_object, logging
from unrealsdk.hooks import Block, Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

MARKER = " ;skip_startup_movies"
STATE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BL2SkipStartup"
STATE_PATH = STATE_DIR / "state.json"
_CREATE_NO_WINDOW = 0x08000000

_REGISTRY_SECTION = (
    "[DataStoreClient_0:UIDataStore_Registry_0.UIRegistryDataProvider UIDynamicFieldProvider]"
)
_REGISTRY_LINES = ("SkipLegalScreen=1",)
_REGISTRY_REMOVE = (
    "GoStraightToMainMenu=1",
    "SkipLegalScreen=1",
    "PendingLoginChange=0",
    "HasBeenThroughTitleScreen=1",
)

_legal_skipped = False
_startup_started = False
_watcher_started = False


def _engine_ini() -> Path | None:
    documents = Path.home() / "Documents"
    names = ("My Games", "My games")
    roots = [documents]
    onedrive = os.environ.get("OneDrive")
    if onedrive:
        roots.append(Path(onedrive) / "Documents")
    for root in roots:
        for folder in names:
            path = root / folder / "Borderlands 2" / "WillowGame" / "Config" / "WillowEngine.ini"
            if path.is_file():
                return path
    return None


def _read_text(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16"), "utf-16"
    return data.decode("utf-8"), "utf-8"


def _write_text(path: Path, text: str, encoding: str) -> None:
    if encoding == "utf-16":
        path.write_bytes(text.encode("utf-16"))
        return
    path.write_bytes(text.encode("utf-8"))


def _newline(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _body(line: str) -> str:
    ending = _newline(line)
    return line[: len(line) - len(ending)] if ending else line


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _is_startup_movie(content: str) -> bool:
    stripped = content.strip()
    if stripped.startswith(";"):
        stripped = stripped[1:].lstrip()
    return stripped.startswith("StartupMovies=") or stripped.startswith("+StartupMovies=")


def _comment_movies(text: str, saved: list[str]) -> tuple[str, list[str]]:
    originals = list(saved)
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        content = _body(line)
        ending = _newline(line)
        stripped = content.lstrip()
        if MARKER in content:
            output.append(line)
            continue
        if stripped.startswith("StartupMovies=") or stripped.startswith("+StartupMovies="):
            if stripped not in originals:
                originals.append(stripped)
            output.append(";" + content + MARKER + ending)
            continue
        output.append(line)
    return "".join(output), originals


def _restore_movies(text: str, originals: list[str]) -> str:
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        if MARKER in line or (
            _body(line).lstrip().startswith(";") and _is_startup_movie(_body(line))
        ):
            continue
        if _body(line).lstrip().startswith("StartupMovies=") or _body(line).lstrip().startswith(
            "+StartupMovies="
        ):
            continue
        kept.append(line)
    if not originals:
        return "".join(kept)
    insert = "".join(item + "\r\n" for item in originals)
    joined = "".join(kept)
    token = "SkippableMovies="
    index = joined.find(token)
    if index < 0:
        return joined + insert
    line_start = joined.rfind("\n", 0, index)
    line_start = 0 if line_start < 0 else line_start + 1
    return joined[:line_start] + insert + joined[line_start:]


def apply_startup_movies(enabled: bool) -> None:
    path = _engine_ini()
    if path is None:
        logging.error("[SkipStartup] 找不到 WillowEngine.ini")
        return
    state = _load_state()
    text, encoding = _read_text(path)
    originals = [str(item) for item in state.get("originals", [])]
    if enabled:
        updated, originals = _comment_movies(text, originals)
    else:
        updated = _restore_movies(text, originals)
    if updated != text:
        _write_text(path, updated, encoding)
    try:
        ui_path = _ui_ini()
        if ui_path is not None:
            ui_text, ui_encoding = _read_text(ui_path)
            ui_updated = _patch_registry_text(ui_text, enabled)
            if ui_updated != ui_text:
                _write_text(ui_path, ui_updated, ui_encoding)
            state["ui"] = str(ui_path)
    except Exception as exc:
        logging.error(f"[SkipStartup] 更新界面配置失败: {exc}")
    state["enabled"] = enabled
    state["originals"] = originals
    state["ini"] = str(path)
    _save_state(state)
    logging.info(
        "[SkipStartup] 开场影片已关闭，下次启动生效"
        if enabled
        else "[SkipStartup] 已恢复开场影片，下次启动生效"
    )


def _watcher_script() -> str:
    return r"""
param(
    [int]$GamePid,
    [string]$StatePath
)
function Apply-SkipStartupState {
if (-not (Test-Path -LiteralPath $StatePath)) { return }
$state = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
$ini = [string]$state.ini
if (-not (Test-Path -LiteralPath $ini)) { return }
$marker = " ;skip_startup_movies"
$text = [IO.File]::ReadAllText($ini)
$lines = $text -split "`r`n|`n", -1
$usesCr = $text.Contains("`r`n")
$nl = if ($usesCr) { "`r`n" } else { "`n" }
$enabled = [bool]$state.enabled
$originals = @()
if ($state.originals) { $originals = @($state.originals) }
if ($enabled) {
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($line in $lines) {
        $trim = $line.TrimStart()
        if ($line.Contains($marker)) { $out.Add($line); continue }
        if ($trim.StartsWith("StartupMovies=") -or $trim.StartsWith("+StartupMovies=")) {
            $out.Add(";" + $line + $marker)
            continue
        }
        $out.Add($line)
    }
    $updated = [string]::Join($nl, $out)
} else {
    $kept = New-Object System.Collections.Generic.List[string]
    foreach ($line in $lines) {
        $trim = $line.TrimStart()
        if ($line.Contains($marker)) { continue }
        if ($trim.StartsWith(";") -and ($trim.TrimStart(";").TrimStart().StartsWith("StartupMovies=") -or $trim.TrimStart(";").TrimStart().StartsWith("+StartupMovies="))) { continue }
        if ($trim.StartsWith("StartupMovies=") -or $trim.StartsWith("+StartupMovies=")) { continue }
        $kept.Add($line)
    }
    $updated = [string]::Join($nl, $kept)
    if ($originals.Count -gt 0) {
        $block = [string]::Join($nl, $originals) + $nl
        $idx = $updated.IndexOf("SkippableMovies=")
        if ($idx -lt 0) {
            $updated = $updated + $block
        } else {
            $lineStart = $updated.LastIndexOf("`n", $idx)
            if ($lineStart -lt 0) { $lineStart = 0 } else { $lineStart += 1 }
            $updated = $updated.Substring(0, $lineStart) + $block + $updated.Substring($lineStart)
        }
    }
}
$utf8 = New-Object System.Text.UTF8Encoding $false
[IO.File]::WriteAllText($ini, $updated, $utf8)
$ui = [string]$state.ui
if (-not $ui -or -not (Test-Path -LiteralPath $ui)) { return }
$uiText = [IO.File]::ReadAllText($ui)
$uiLines = $uiText -split "`r`n|`n", -1
$uiNl = if ($uiText.Contains("`r`n")) { "`r`n" } else { "`n" }
$section = "[DataStoreClient_0:UIDataStore_Registry_0.UIRegistryDataProvider UIDynamicFieldProvider]"
$remove = @(
    "GoStraightToMainMenu=1",
    "SkipLegalScreen=1",
    "PendingLoginChange=0",
    "HasBeenThroughTitleScreen=1"
)
$keys = @("SkipLegalScreen=1")
$sectionAt = -1
for ($i = 0; $i -lt $uiLines.Count; $i++) {
    if ($uiLines[$i].Trim() -eq $section) { $sectionAt = $i; break }
}
if ($sectionAt -lt 0) {
    if ($enabled) {
        $uiText = $uiText.TrimEnd() + $uiNl + $section + $uiNl + ($keys -join $uiNl) + $uiNl
        [IO.File]::WriteAllText($ui, $uiText, $utf8)
    }
    return
}
$sectionEnd = $uiLines.Count
for ($i = $sectionAt + 1; $i -lt $uiLines.Count; $i++) {
    if ($uiLines[$i].StartsWith("[")) { $sectionEnd = $i; break }
}
$keptUi = New-Object System.Collections.Generic.List[string]
for ($i = 0; $i -lt $uiLines.Count; $i++) {
    if ($i -gt $sectionAt -and $i -lt $sectionEnd -and $remove -contains $uiLines[$i].Trim()) { continue }
    $keptUi.Add($uiLines[$i])
}
if ($enabled) {
    $insertAt = $sectionAt + 1
    for ($k = $keys.Count - 1; $k -ge 0; $k--) { $keptUi.Insert($insertAt, $keys[$k]) }
}
[IO.File]::WriteAllText($ui, ([string]::Join($uiNl, $keptUi)), $utf8)
}
Wait-Process -Id $GamePid -ErrorAction SilentlyContinue
foreach ($n in 1..12) {
    Start-Sleep -Milliseconds 500
    Apply-SkipStartupState
}
"""


def _ensure_watcher() -> None:
    global _watcher_started
    if _watcher_started:
        return
    state = _load_state()
    if not state.get("ini"):
        return
    _watcher_started = True
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    script_path = STATE_DIR / "reapply.ps1"
    script_path.write_text(_watcher_script(), encoding="utf-8-sig")
    launcher_path = STATE_DIR / "reapply.vbs"
    launcher_path.write_text(
        "\r\n".join(
            (
                'Set shell = CreateObject("Wscript.Shell")',
                "shell.Run \"powershell.exe -NoProfile -NonInteractive "
                "-ExecutionPolicy Bypass -WindowStyle Hidden -File "
                f'""{script_path}"" -GamePid {os.getpid()} -StatePath ""{STATE_PATH}"""", 0, True',
                "",
            )
        ),
        encoding="ascii",
    )
    command_line = f'wscript.exe //B //Nologo "{launcher_path}"'
    escaped = command_line.replace("'", "''")
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-Command",
            "[void](Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
            f"-Arguments @{{ CommandLine = '{escaped}' }})",
        ],
        creationflags=_CREATE_NO_WINDOW,
        check=False,
    )


def _ui_ini() -> Path | None:
    engine = _engine_ini()
    if engine is None:
        return None
    path = engine.with_name("WillowUI.ini")
    return path if path.is_file() else None


def _patch_registry_text(text: str, enabled: bool) -> str:
    lines = text.splitlines(keepends=True)
    section_at = next(
        (index for index, line in enumerate(lines) if _body(line).strip() == _REGISTRY_SECTION),
        None,
    )
    if section_at is None:
        if not enabled:
            return text
        ending = "\r\n" if "\r\n" in text else "\n"
        extra = ending.join(("", _REGISTRY_SECTION, *_REGISTRY_LINES, ""))
        return text + extra
    end = len(lines)
    for index in range(section_at + 1, len(lines)):
        if _body(lines[index]).startswith("["):
            end = index
            break
    body = [
        line
        for line in lines[section_at + 1 : end]
        if _body(line).strip() not in _REGISTRY_REMOVE
    ]
    if enabled:
        ending = _newline(lines[section_at]) or "\r\n"
        body = [item + ending for item in _REGISTRY_LINES] + body
    return "".join([*lines[: section_at + 1], *body, *lines[end:]])


def _press_start_movies() -> list[UObject]:
    try:
        found = find_all("WillowGame.WillowGFxMoviePressStart", False)
    except Exception:
        return []
    movies: list[UObject] = []
    for movie in found:
        if "Default__" in str(movie):
            continue
        movies.append(movie)
    return movies


def _begin_startup(movie: UObject) -> None:
    global _startup_started
    if _startup_started:
        return
    try:
        movie.BeginStartupProcess()
        _startup_started = True
    except Exception as exc:
        logging.error(f"[SkipStartup] 启动 DLC 和 SHiFT 检查失败: {exc}")


def _hide_legal(movie: UObject) -> bool:
    hidden = False
    for action in (
        lambda: movie.SetVariableBool("legal._visible", False),
        lambda: movie.SetVariableBool("_root.legal._visible", False),
        lambda: movie.SetVariableString("legal.gearbox.text", ""),
        lambda: movie.ActionScript("legal.gotoAndStop"),
        lambda: movie.GotoAndStop("press_start"),
    ):
        try:
            action()
            hidden = True
        except Exception:
            pass
    return hidden


def _skip_legal_frames(movie: UObject | None = None) -> None:
    global _legal_skipped
    if _legal_skipped:
        return
    movies = [movie] if movie is not None else _press_start_movies()
    for current in movies:
        if current is None or "Default__" in str(current):
            continue
        if _hide_legal(current):
            _begin_startup(current)
            _legal_skipped = True
            logging.info("[SkipStartup] 已隐藏版权页")
            return


@hook("WillowGame.WillowGFxMoviePressStart:CustomPlay", Type.PRE)
def on_custom_play(
    obj: UObject,
    _args: WrappedStruct,
    _ret: Any,
    _func: BoundFunction,
) -> type[Block] | None:
    _set_skip_registry()
    _hide_legal(obj)
    _begin_startup(obj)
    return Block


def _set_skip_registry() -> bool:
    targets: list[Any] = []
    try:
        ui_root = find_class("Engine.UIRoot")
    except Exception as exc:
        logging.error(f"[SkipStartup] 找不到 UIRoot: {exc}")
        return False
    try:
        targets.append(find_object("Engine.UIRoot", "Engine.Default__UIRoot"))
    except Exception:
        pass
    targets.append(ui_root)
    keys = ("<Registry:SkipLegalScreen>",)
    for target in targets:
        try:
            for key in keys:
                target.SetDataStoreStringValue(key, "1")
            return True
        except Exception as exc:
            logging.info(f"[SkipStartup] 注册表写入尝试失败: {exc}")
    return False


@hook("WillowGame.WillowGFxMoviePressStart:Start", Type.PRE)
def on_press_start(
    _obj: UObject,
    _args: WrappedStruct,
    _ret: Any,
    _func: BoundFunction,
) -> None:
    global _legal_skipped, _startup_started
    _legal_skipped = False
    _startup_started = False
    _set_skip_registry()


@hook("WillowGame.WillowGFxMoviePressStart:Start", Type.POST)
def on_press_start_done(
    obj: UObject,
    _args: WrappedStruct,
    _ret: Any,
    _func: BoundFunction,
) -> None:
    _skip_legal_frames(obj)


@hook("WillowGame.WillowGFxMoviePressStart:extSetLegalText", Type.POST)
def on_legal_text(
    obj: UObject,
    _args: WrappedStruct,
    _ret: Any,
    _func: BoundFunction,
) -> None:
    _skip_legal_frames(obj)


def on_enable() -> None:
    apply_startup_movies(True)
    _ensure_watcher()
    _skip_legal_frames()


def on_disable() -> None:
    apply_startup_movies(False)


build_mod(
    on_enable=on_enable,
    on_disable=on_disable,
    supported_games=Game.BL2,
    coop_support=CoopSupport.ClientSide,
)
