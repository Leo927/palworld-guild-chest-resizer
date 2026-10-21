# Palworld Guild Chest Resizer

[![tests](https://github.com/Leo927/palworld-guild-chest-resizer/actions/workflows/tests.yml/badge.svg)](https://github.com/Leo927/palworld-guild-chest-resizer/actions/workflows/tests.yml)

Resize the Guild Chest in an **existing** Palworld save, keeping everything already
stored in it. Works on Palworld **0.6+** saves, which most save editors cannot open.

Single player and dedicated server. No mods, no UE4SS, no PalSchema.

```
> run.ps1 list

Save: E:\...\SaveGames\0\<world>\Level.sav
Format: PlM (Oodle)

  guild group  : c4f85b2a-4a61-c738-8162-6d9b8e98759a
  container_id : 51551d22-4f25-8d2b-1b19-91a5fe44f6cf
  slots        : 54
  occupied     : 46 (highest slot index 45)
      [   0]   9999  Wood
      [  13]   9999  Stone
      ...

> run.ps1 resize 358
```

---

## Why this exists

The Guild Chest has 54 slots and there is no in-game way to change that. The
widely-copied advice for existing saves is to regenerate the chest's `container_id` so
the game rebuilds it at a modded size. **That advice destroys everything in your
chest** and leaves an orphaned container in your save forever.

The chest's capacity is a single integer in the save file. This tool changes that
integer. Your items stay exactly where they are.

There is a second problem. Palworld 0.6 ("Tides of Terraria") switched save
compression from zlib to **Oodle**, changing the file magic from `PlZ` to `PlM`. Every
tool built on `palworld-save-tools` — including the Palworld Save Tools GUI, PalEdit,
and the older resizer scripts — fails immediately:

```
Exception: not a compressed Palworld save, found b'PlM' instead of b'PlZ'
```

This project bundles the patches that fix that, so it reads modern saves out of the box.

## Do I need a mod?

**No.** PalSchema mods that set `GuildChestSlotNum` only apply **when a container is
created**, so they cannot resize a chest that already exists — which is exactly why
save editing is needed at all.

Verified on a dedicated server: the save edit alone gives the resized chest with no
UE4SS and no PalSchema installed. The game honours the slot count stored in the save.

A mod is only worth it if you want *new, separate* guilds to get big chests
automatically. Weigh that against UE4SS being version-locked to your Palworld build —
it can break server startup on every patch.

---

## Requirements

- **Python 3.9+** on PATH ([python.org](https://www.python.org/downloads/) — tick
  "Add Python to PATH" during install)
- Windows for the automatic setup. Linux/macOS work too, with one manual step (below).
- Internet access on first run, to fetch the dependency and the Oodle decompressor.

## Setup

Windows (PowerShell, in the project folder):

```powershell
.\setup.ps1
```

Linux/macOS:

```bash
./setup.sh
```

This creates a `venv/`, installs the pinned `palworld-save-tools`, applies the 0.6
patches, and downloads `libooz.dll`. Re-running it is harmless.

> **Linux/macOS:** upstream publishes Windows binaries of `ooz` only. Build it
> yourself (`git clone https://github.com/zao/ooz && cmake -B build
> -DCMAKE_BUILD_TYPE=Release && cmake --build build`), then drop the shared library at
> `ooz/libooz.so` or set `PALWORLD_OOZ_DLL_PATH` to it. Only needed for 0.6+ (`PlM`)
> saves.

## Usage

### 1. Back up your save

The tool makes its own backup, but keep an independent copy. Copy the whole world
folder — the one containing `Level.sav`, `LevelMeta.sav` and `Players/`.

### 2. Stop the game or server

**This matters.** A running server keeps the world in memory and rewrites `Level.sav`
on its autosave timer, which will silently throw away your edit. Also stop anything
that auto-restarts it — a server manager restarting mid-edit can corrupt the save.

The tool refuses to run while it can see a server process, but it cannot see a manager
that restarts one behind its back.

### 3. Look at what you have

```powershell
.\run.ps1 list
```

`Level.sav` is auto-detected for the usual dedicated-server and single-player
locations. If it finds several worlds it lists them; pick one with `--save`:

```powershell
.\run.ps1 list --save "C:\Users\you\AppData\Local\Pal\Saved\SaveGames\<id>\<world>\Level.sav"
```

If it reports no guild chest, your guild has not built one yet. Build it in game
first — the container does not exist until then.

### 4. Resize

```powershell
.\run.ps1 resize 358
```

It shows the before state, asks for confirmation, applies the change, **reads back the
file it wrote** to confirm both the new slot count and unchanged contents, backs up
your original to `backups/`, and only then installs. Any failure leaves your save
untouched.

Add `-y` to skip the prompt.

**Multi-guild servers:** by default *every* guild's chest is resized. Guilds that have
not built a chest yet are skipped, because the container does not exist in the save
until they do — they will get the vanilla 54 when they build one. Use
`--container <id>` (from `list`) to resize a single guild instead.

If the new size would strand items, the tool reports **every** affected guild at once
and writes nothing, so you are not fixing them one re-run at a time:

```
REFUSING: 10 slots would strand items already in the chest.

  guild c4f85b2a-4a61-c738-8162-6d9b8e98759a
    container 51551d22-4f25-8d2b-1b19-91a5fe44f6cf
    104 items, highest slot index 103

Use more than 103 slots, or move those items out of the affected chests.
Nothing was written.
```

### 5. Start the game and open the chest

---

## How big should I go?

Vanilla is 54. **Don't go wild** — every slot is serialised into `Level.sav` on every
save and laid out by the UI when the chest opens.

| Slots | Result |
|------:|--------|
| 54 | vanilla |
| 150–360 | comfortable; no noticeable delay |
| ~2500 | measured **~20 second freeze** every time the chest opens |

A few hundred is plenty. Re-running with a different number takes about a minute, so
tune it.

The tool refuses to shrink the chest to at or below the highest occupied slot index,
because sparse slots above the new limit would become unreachable. Move those items out
first, or pick a larger number. `list` shows the highest index in use.

---

## Rolling back

Every `resize` prints the exact command. Backups land in `backups/`:

```powershell
copy "backups\Level_2026-07-25_00-21-46.sav" "<path to your Level.sav>"
```

Stop the server before restoring, same as before.

## Notes and caveats

- **Edited saves get bigger, temporarily.** Output is written as zlib (`PlZ`) rather
  than Oodle — there is no open Oodle *compressor*. A 1.3 MB save becomes about 1.6 MB.
  Palworld reads it fine, and the next time the game saves the world it writes Oodle
  again and the size drops back. This is the same approach every current save tool uses.
- **Newer save fields.** The pinned library does not understand `SetProperty`, used
  by fields such as `worldSaveData.InLockerCharacterInstanceIDArray`. The editor
  preserves sets and unrelated world properties as their original bytes. It only
  decodes the guild storage map and item containers, without using the library's
  version-dependent custom `RawData` decoders. A `Struct type for ... not found`
  message can still appear when a type hint is absent.
- **Compatibility checks.** Every load must reproduce the decompressed input
  byte for byte. Before writing, restoring only the original guild chest capacities
  must reproduce that same input; any other change is rejected before the output
  file is opened. Unknown property framing is rejected rather than guessed. A
  compatibility error means this save needs further support; do not bypass the check.
- **Don't install this inside `steamapps`.** Steam's "Verify integrity of game files"
  can delete anything it finds under `steamapps/common`.
- **Version pinning.** `patches/` is derived from `palworld-save-tools` 0.24.0
  specifically. Don't bump `requirements.txt` without re-checking them.
- **Xbox/Game Pass saves** (`CNK` magic) are not tested.

## How it works

For anyone who wants to verify the approach rather than trust it:

- `worldSaveData.GuildExtraSaveDataMap[guild].GuildItemStorage.RawData` holds the
  chest's container id as a raw **FGuid — four little-endian uint32s**. There is no
  readable `"container_id"` string to search for in current saves, contrary to the
  usual instructions. Bytes `22 1D 55 51 | 2B 8D 25 4F | A5 91 19 1B | CF F6 44 FE`
  decode to `51551d22-4f25-8d2b-1b19-91a5fe44f6cf`.
- That id keys an entry in `worldSaveData.ItemContainerSaveData`, whose **`SlotNum`**
  is the capacity. That is the only field this tool writes.
- `Slots` is stored **sparsely by index** and need not match `SlotNum`; the game
  compacts indices when it loads the world. Each slot's `RawData` is
  `int32 index, int32 stack count, length-prefixed item id`.

Because capacity and stored slots are decoupled, resizing is one integer and no item
data is touched. `save_compat.py` supplies tool-specific reader/writer subclasses;
it does not change the installed library's global behavior. Unrelated top-level
world fields retain their type metadata and payload bytes, including data that the
old library cannot interpret.

## Development

Tests use synthetic save-shaped dictionaries and binary zlib saves. They need no
private save files, network access, or native libooz:

```powershell
.\venv\Scripts\python.exe -m unittest discover -p "test_*.py" -v
```

The same command runs in CI on Linux and Windows across Python 3.9 and 3.12. Coverage
includes the multi-guild and `--container` paths, the shrink guard and its exact
boundary, guilds with no chest built, saves with no guilds at all, and the FGuid and
slot decoders against a known vector from a real save. Binary regression tests
cover opaque sets and property headers, malformed input, exact read/write
round trips, a 54-to-358 resize, and rejection of unrelated changes without
creating or truncating the output file.

### Verification done on this approach

A full `Level.sav -> JSON -> Level.sav -> JSON` round trip on an unmodified 0.6 save
produced a **byte-identical** 119 MB JSON in both directions, proving the patched
pipeline is lossless. The first real edit was then diffed to **exactly one changed
line** out of that 119 MB. Every `resize` re-verifies the file it wrote before
installing it.

## Credits

- [cheahjs/palworld-save-tools](https://github.com/cheahjs/palworld-save-tools) — MIT.
  The save (de)serialiser everything here stands on.
- [quadrantbs/palworld-hostfix-toolkit](https://github.com/quadrantbs/palworld-hostfix-toolkit)
  — MIT. Source of the `PlM`/Oodle support and the 0.6 `rawdata` parsers in `patches/`.
- [zao/ooz](https://github.com/zao/ooz) — clean-room Oodle Kraken decompressor,
  downloaded at setup, **not redistributed** (upstream publishes no license).
- [iBug's writeup](https://ibug.io/blog/2025/07/palworld-0.6-resize-guild-chest/) —
  identified the `PlM`/Oodle change and confirmed the game still accepts zlib saves.

See [NOTICE.md](NOTICE.md) for full license texts. This project is MIT — see
[LICENSE](LICENSE).
