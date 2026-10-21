#!/usr/bin/env python3
"""Resize the Palworld guild chest by editing Level.sav.

Works on Palworld 0.6+ saves (Oodle / "PlM" compression) which no off-the-shelf
save editor can read. Preserves everything already in the chest.

    python guild_chest.py list
    python guild_chest.py resize 358
    python guild_chest.py resize 358 --save "C:\\path\\to\\Level.sav"

See README.md for setup.
"""

import os
import pathlib
import platform
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent

# palworld_save_tools.palsav reads this env var at import time, so it has to be set
# before the import below.
if "PALWORLD_OOZ_DLL_PATH" not in os.environ:
    for _name in ("libooz.dll", "libooz.so", "libooz.dylib"):
        _candidate = HERE / "ooz" / _name
        if _candidate.exists():
            os.environ["PALWORLD_OOZ_DLL_PATH"] = str(_candidate)
            break

import argparse  # noqa: E402
import struct  # noqa: E402

try:
    from palworld_save_tools.palsav import compress_gvas_to_sav, decompress_sav_to_gvas
    from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS
    from save_compat import SaveCompatibilityError, read_gvas, write_gvas
except ImportError:
    sys.exit(
        "palworld_save_tools is not installed.\n"
        "Run setup.ps1 (Windows) or setup.sh (Linux/macOS) first, and make sure you are\n"
        "using the Python from the created venv."
    )

SERVER_PROCESS_NAMES = (
    "PalServer-Win64-Shipping",
    "PalServer-Win64-Shipping-Cmd",
    "PalServer-Linux-Shipping",
    "PalServer-Linux-Test",
    "Palworld-Win64-Shipping",
)


# --------------------------------------------------------------------------- save I/O


def guid_from_bytes(raw):
    """Palworld serialises an FGuid as four little-endian uint32s."""
    a, b, c, d = struct.unpack_from("<IIII", bytes(x & 0xFF for x in raw[:16]), 0)
    h = f"{a:08x}{b:08x}{c:08x}{d:08x}"
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def decode_slot(raw):
    """Slot RawData: int32 slot index, int32 stack count, length-prefixed item id."""
    b = bytes(x & 0xFF for x in raw)
    index, count, strlen = struct.unpack_from("<iii", b, 0)
    name = b[12 : 12 + strlen - 1].decode("utf-8", "replace") if strlen > 0 else ""
    return index, count, name


def load_save(path):
    with open(path, "rb") as f:
        data = f.read()
    magic = data[8:11]
    try:
        raw_gvas, save_type = decompress_sav_to_gvas(data)
    except Exception as exc:
        if magic == b"PlM":
            sys.exit(
                f"Failed to decompress {path}: {exc}\n\n"
                "This is an Oodle-compressed (PlM) save and libooz could not be used.\n"
                "Run the setup script, or set PALWORLD_OOZ_DLL_PATH to your libooz build."
            )
        sys.exit(f"Failed to decompress {path}: {exc}")
    gvas = read_gvas(raw_gvas, PALWORLD_TYPE_HINTS)
    gvas._original_raw = raw_gvas
    wsd = gvas.properties["worldSaveData"]["value"]
    containers = containers_by_id(wsd)
    gvas._original_chest_slots = [
        (containers[cid]["SlotNum"], containers[cid]["SlotNum"]["value"])
        for cid in set(guild_storage_container_ids(wsd).values()) if cid in containers
    ]
    return gvas, save_type, magic


def write_save(gvas, path):
    # Mirrors palworld-save-tools: the save type comes from the save game class, not
    # from whatever the input file used. Output is always zlib (PlZ), which the game
    # accepts even for saves it originally wrote as Oodle.
    save_type = (
        0x32
        if (
            "Pal.PalWorldSaveGame" in gvas.header.save_game_class_name
            or "Pal.PalLocalWorldSaveGame" in gvas.header.save_game_class_name
        )
        else 0x31
    )
    edited_raw = write_gvas(gvas)
    # Restoring only the allowed capacities must reproduce the entire input.
    # Check before opening the output, so even an existing file stays intact.
    originals = gvas._original_chest_slots
    edited_values = [prop["value"] for prop, old in originals]
    try:
        for prop, old in originals:
            prop["value"] = old
        if write_gvas(gvas) != gvas._original_raw:
            raise SaveCompatibilityError(
                "Unexpected non-capacity change; refusing to write save"
            )
    finally:
        for (prop, old), edited in zip(originals, edited_values):
            prop["value"] = edited
    encoded = compress_gvas_to_sav(edited_raw, save_type)
    with open(path, "wb") as f:
        f.write(encoded)


# ------------------------------------------------------------------- guild chest data


def guild_storage_container_ids(wsd):
    """{guild group id: guild chest container id}."""
    out = {}
    extra = wsd.get("GuildExtraSaveDataMap")
    if not extra:
        return out
    for entry in extra["value"]:
        storage = entry["value"].get("GuildItemStorage")
        if not storage:
            continue
        out[entry["key"]] = guid_from_bytes(storage["value"]["RawData"]["value"]["values"])
    return out


def containers_by_id(wsd):
    return {
        entry["key"]["ID"]["value"]: entry["value"]
        for entry in wsd["ItemContainerSaveData"]["value"]
    }


def survey(wsd):
    """{container id: {slotnum, max_index, occupied, guild, items}} for guild chests."""
    containers = containers_by_id(wsd)
    result = {}
    for guild_id, container_id in guild_storage_container_ids(wsd).items():
        container = containers.get(container_id)
        if container is None:
            print(f"  ! guild {guild_id}: container {container_id} missing from save")
            continue
        decoded = [
            decode_slot(s["RawData"]["value"]["values"])
            for s in container["Slots"]["value"]["values"]
        ]
        result[container_id] = {
            "guild": guild_id,
            "slotnum": container["SlotNum"]["value"],
            "max_index": max((d[0] for d in decoded), default=-1),
            "occupied": sum(1 for d in decoded if d[2]),
            "items": [(i, c, n) for i, c, n in decoded if n],
        }
    return result


def print_survey(data, show_items=True):
    if not data:
        print("No guild chests found in this save.")
        print("A guild chest container only exists once a guild has built one in game.")
        return
    for container_id, info in data.items():
        print(f"  guild group  : {info['guild']}")
        print(f"  container_id : {container_id}")
        print(f"  slots        : {info['slotnum']}")
        print(f"  occupied     : {info['occupied']} (highest slot index {info['max_index']})")
        if show_items and info["items"]:
            for index, count, name in info["items"]:
                print(f"      [{index:>4}] {count:>6}  {name}")
        print()


class UnsafeShrink(Exception):
    """Raised when the requested slot count would strand items in unreachable slots."""

    def __init__(self, offenders, new_slots):
        self.offenders = offenders
        self.new_slots = new_slots
        super().__init__(self.message())

    def message(self):
        worst = max(o["max_index"] for o in self.offenders)
        lines = [
            f"REFUSING: {self.new_slots} slots would strand items already in the chest.",
            "",
        ]
        for o in self.offenders:
            lines.append(
                f"  guild {o['guild']}"
                f"\n    container {o['container_id']}"
                f"\n    {o['occupied']} items, highest slot index {o['max_index']}"
            )
        lines += [
            "",
            f"Use more than {worst} slots, or move those items out of the affected chests.",
            "Nothing was written.",
        ]
        return "\n".join(lines)


def apply_slot_count(wsd, new_slots, container_filter=None):
    """Set SlotNum on every guild chest (or just container_filter). All-or-nothing."""
    containers = containers_by_id(wsd)
    targets, offenders = [], []

    for guild_id, container_id in guild_storage_container_ids(wsd).items():
        if container_filter and container_id != container_filter:
            continue
        container = containers.get(container_id)
        if container is None:
            continue
        decoded = [
            decode_slot(s["RawData"]["value"]["values"])
            for s in container["Slots"]["value"]["values"]
        ]
        max_index = max((d[0] for d in decoded), default=-1)
        # Slots are stored sparsely by index. Shrinking to at or below the highest
        # occupied index would strand real items in slots the chest cannot address.
        if new_slots <= max_index:
            offenders.append(
                {
                    "guild": guild_id,
                    "container_id": container_id,
                    "max_index": max_index,
                    "occupied": sum(1 for d in decoded if d[2]),
                }
            )
            continue
        targets.append((container_id, container))

    # Every guild is checked before anything is modified, so one over-full chest
    # reports alongside the others instead of hiding them behind an early exit.
    if offenders:
        raise UnsafeShrink(offenders, new_slots)

    changed = []
    for container_id, container in targets:
        old = container["SlotNum"]["value"]
        container["SlotNum"]["value"] = new_slots
        changed.append((container_id, old, new_slots))
    return changed


# ------------------------------------------------------------------------- environment


def discover_saves():
    """Best-effort search for Level.sav in the usual dedicated-server and solo spots."""
    roots, globbed = [], []
    for base in (HERE, pathlib.Path.cwd()):
        for up in (base, *base.parents):
            roots.append(up / "Pal" / "Saved" / "SaveGames")
            roots.append(up / "Saved" / "SaveGames")
            # Catches the common layout where this tool lives beside, not inside, the
            # Steam library: <somewhere>/steamapps/common/PalServer/Pal/Saved/SaveGames
            globbed.append((up / "steamapps" / "common", "*/Pal/Saved/SaveGames"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(pathlib.Path(local) / "Pal" / "Saved" / "SaveGames")

    for parent, pattern in globbed:
        if parent.is_dir():
            try:
                roots.extend(p for p in parent.glob(pattern) if p.is_dir())
            except OSError:
                continue

    found, seen = [], set()
    for root in roots:
        key = str(root).lower()
        if key in seen or not root.is_dir():
            continue
        seen.add(key)
        try:
            found.extend(sorted(root.glob("*/*/Level.sav")))
        except OSError:
            continue
    unique, seen_files = [], set()
    for path in found:
        rp = str(path.resolve()).lower()
        if rp not in seen_files:
            seen_files.add(rp)
            unique.append(path)
    return unique


def running_servers():
    """Names of running Palworld server processes, or None if the check failed."""
    try:
        if platform.system() == "Windows":
            out = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=30, check=True,
            ).stdout
            running = [n for n in SERVER_PROCESS_NAMES if f'"{n}.exe"' in out]
        else:
            out = subprocess.run(
                ["ps", "-eo", "comm="], capture_output=True, text=True, timeout=30, check=True
            ).stdout
            names = set(out.split())
            running = [n for n in SERVER_PROCESS_NAMES if n in names]
        return running
    except Exception:
        return None


def resolve_save(explicit):
    if explicit:
        path = pathlib.Path(explicit)
        if not path.is_file():
            sys.exit(f"No such file: {path}")
        return path
    candidates = discover_saves()
    if not candidates:
        sys.exit(
            "Could not find Level.sav automatically.\n"
            "Pass it explicitly:  python guild_chest.py list --save <path to Level.sav>"
        )
    if len(candidates) > 1:
        print("Multiple saves found. Pick one with --save (most recently played first):\n")
        for path in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True):
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
            print(f"  {when}   {path}")
        sys.exit(1)
    return candidates[0]


# ------------------------------------------------------------------------------ commands


def cmd_list(args):
    path = resolve_save(args.save)
    print(f"Save: {path}")
    gvas, _, magic = load_save(path)
    print(f"Format: {magic.decode('ascii', 'replace')} "
          f"({'Oodle' if magic == b'PlM' else 'zlib'})\n")
    print_survey(survey(gvas.properties["worldSaveData"]["value"]))


def cmd_resize(args):
    path = resolve_save(args.save)
    if args.slots < 1:
        sys.exit("Slot count must be at least 1.")

    servers = running_servers()
    if servers and not args.force:
        sys.exit(
            f"Palworld server is running ({', '.join(servers)}).\n"
            "Stop it before editing the save, or a later autosave will overwrite your edit.\n"
            "Also stop anything that auto-restarts it.\n\n"
            "If you are editing a copy of a save rather than the live one, pass --force."
        )
    if servers and args.force:
        print(f"! Server running ({', '.join(servers)}); --force given, continuing.")
        print("! If this is the live save, the server will overwrite this edit.\n")
    if servers is None:
        print("! Could not check for a running server. Make sure Palworld is closed.\n")

    print(f"Save: {path}")
    gvas, _, _ = load_save(path)
    wsd = gvas.properties["worldSaveData"]["value"]
    before = survey(wsd)
    if not before:
        sys.exit("No guild chest found in this save; nothing to resize.")
    print("Before:")
    print_survey(before, show_items=False)

    if not args.yes:
        reply = input(f"Set guild chest slots to {args.slots}? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            sys.exit("Aborted; nothing written.")

    try:
        changed = apply_slot_count(wsd, args.slots, args.container)
    except UnsafeShrink as exc:
        sys.exit(str(exc))
    if not changed:
        sys.exit("No matching guild chest container; nothing written.")
    for container_id, old, new in changed:
        print(f"  {container_id}: {old} -> {new} slots")

    staged = path.with_suffix(path.suffix + ".new")
    write_save(gvas, staged)

    # Prove the change landed and nothing else moved, by reading back what we wrote.
    verify_gvas, _, _ = load_save(staged)
    after = survey(verify_gvas.properties["worldSaveData"]["value"])
    if set(after) != set(before):
        staged.unlink(missing_ok=True)
        sys.exit("VERIFY FAILED: container set changed. Original save untouched.")
    for container_id, info in after.items():
        old_info = before[container_id]
        expected = args.slots if (not args.container or container_id == args.container) else old_info["slotnum"]
        if info["slotnum"] != expected:
            staged.unlink(missing_ok=True)
            sys.exit(
                f"VERIFY FAILED: {container_id} has {info['slotnum']} slots, expected "
                f"{expected}. Original save untouched."
            )
        if info["items"] != old_info["items"]:
            staged.unlink(missing_ok=True)
            sys.exit(
                f"VERIFY FAILED: {container_id} contents changed. Original save untouched."
            )
    print("Verified: slot count applied, chest contents unchanged.")

    backup_dir = pathlib.Path(args.backup_dir) if args.backup_dir else HERE / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"Level_{time.strftime('%Y-%m-%d_%H-%M-%S')}.sav"
    shutil.copy2(path, backup)
    print(f"Backup: {backup}")

    shutil.move(str(staged), str(path))
    print(f"Installed: {path}")
    print("\nAfter:")
    print_survey(survey(load_save(path)[0].properties["worldSaveData"]["value"]),
                 show_items=False)
    print(f'Roll back with:\n  copy "{backup}" "{path}"')


def main():
    parser = argparse.ArgumentParser(
        prog="guild_chest.py",
        description="Inspect and resize the Palworld guild chest in Level.sav.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="show guild chests and their contents")
    p_list.add_argument("--save", help="path to Level.sav (auto-detected if omitted)")
    p_list.set_defaults(func=cmd_list)

    p_resize = sub.add_parser("resize", help="change the guild chest slot count")
    p_resize.add_argument("slots", type=int, help="new slot count (vanilla is 54)")
    p_resize.add_argument("--save", help="path to Level.sav (auto-detected if omitted)")
    p_resize.add_argument("--container", help="only resize this container id")
    p_resize.add_argument("--backup-dir", help="where to put the backup (default: ./backups)")
    p_resize.add_argument("-y", "--yes", action="store_true", help="skip the confirmation")
    p_resize.add_argument(
        "--force",
        action="store_true",
        help="proceed even if a server is running (only safe when editing a copy)",
    )
    p_resize.set_defaults(func=cmd_resize)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
