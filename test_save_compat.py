"""Synthetic binary saves: no private worlds, native Oodle library, or network."""

import contextlib
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

import guild_chest as gc
from palworld_save_tools.archive import FArchiveReader, FArchiveWriter
from palworld_save_tools.gvas import GvasHeader
from save_compat import (
    ChestReader, ChestWriter, SaveCompatibilityError, read_gvas, write_gvas,
)
from test_guild_chest import guid_to_bytes, slot_bytes


ZERO = "00000000-0000-0000-0000-000000000000"
GUILD = "11111111-1111-1111-1111-111111111111"
CHEST = "22222222-2222-2222-2222-222222222222"
OTHER = "33333333-3333-3333-3333-333333333333"


def fstring(text):
    return struct.pack("<i", len(text) + 1) + text.encode("ascii") + b"\x00"


END = fstring("None")


def record(name, kind, payload, metadata=b"\x00"):
    return fstring(name) + fstring(kind) + struct.pack("<Q", len(payload)) + metadata + payload


def structure(value, kind="StructProperty"):
    return {"type": "StructProperty", "struct_type": kind, "struct_id": ZERO, "value": value}


def byte_array(data):
    return {"type": "ArrayProperty", "array_type": "ByteProperty",
            "value": {"values": list(data)}}


def map_property(entries, key_struct="Guid"):
    return {
        "type": "MapProperty", "key_type": "StructProperty", "value_type": "StructProperty",
        "key_struct_type": key_struct, "value_struct_type": "StructProperty", "value": entries,
    }


def fixture(extra_records=b"", include_set=True):
    """Serialize supported fields with the upstream writer, new sets by hand."""
    header = GvasHeader.load({
        "magic": 0x53415647, "save_game_version": 3,
        "package_file_version_ue4": 522, "package_file_version_ue5": 1009,
        "engine_version_major": 5, "engine_version_minor": 1,
        "engine_version_patch": 1, "engine_version_changelist": 0,
        "engine_version_branch": "synthetic-test", "custom_version_format": 3,
        "custom_versions": [], "save_game_class_name": "/Script/Pal.PalWorldSaveGame",
    })
    containers = []
    for cid, capacity in ((CHEST, 54), (OTHER, 20)):
        slots = {
            "type": "ArrayProperty", "array_type": "StructProperty",
            "value": {
                "prop_name": "Slots", "prop_type": "StructProperty",
                "type_name": "PalItemSlotSaveData", "id": ZERO,
                "values": [{"RawData": byte_array(slot_bytes(0, 100, "Wood"))}],
            },
        }
        containers.append({
            "key": {"ID": structure(cid, "Guid")},
            "value": {"SlotNum": {"type": "IntProperty", "value": capacity}, "Slots": slots},
        })
    world = {
        "GuildExtraSaveDataMap": map_property([{
            "key": GUILD, "value": {
                "GuildItemStorage": structure({"RawData": byte_array(guid_to_bytes(CHEST))})
            },
        }]),
        "ItemContainerSaveData": map_property(containers, key_struct="StructProperty"),
    }
    w = FArchiveWriter()
    w.properties(world)
    body = w.bytes()[:-len(END)]
    # A map with a payload the old library cannot decode. The editor never needs it.
    body += record("UnrelatedMap", "MapProperty", bytes(range(37)),
                   fstring("FutureKeyProperty") + fstring("FutureValueProperty") + b"\x00")
    if include_set:
        body += record("InLockerCharacterInstanceIDArray", "SetProperty",
                       struct.pack("<II", 0, 2) + bytes(range(32)),
                       fstring("StructProperty") + b"\x00")
    body += extra_records + END
    w = FArchiveWriter()
    header.write(w)
    return (w.bytes() + record("worldSaveData", "StructProperty", body,
                              fstring("PalWorldSaveData") + bytes(16) + b"\x00")
            + END + bytes(4))


class TestOpaqueProperties(unittest.TestCase):
    def roundtrip(self, data, path=".worldSaveData"):
        r = ChestReader(data)
        props = r.properties_until_end(path)
        self.assertEqual(r.data.tell(), len(data))
        w = ChestWriter()
        w.properties(props)
        self.assertEqual(w.bytes(), data)
        return props

    def test_set_payloads_and_optional_guid_preserved(self):
        for payload in (struct.pack("<II", 0, 0),
                        struct.pack("<II", 0, 2) + bytes(range(32)),
                        struct.pack("<I", 1) + bytes(16) + struct.pack("<I", 0)):
            for guid in (b"\x00", b"\x01" + bytes(range(16))):
                with self.subTest(payload=payload, guid=guid):
                    data = record("NewSet", "SetProperty", payload,
                                  fstring("StructProperty") + guid)
                    data += record("After", "IntProperty", struct.pack("<i", 42)) + END
                    props = self.roundtrip(data)
                    self.assertIn("_preserved_body", props["NewSet"])

    def test_known_framing_preserves_metadata_without_decoding_payload(self):
        cases = {
            "StructProperty": fstring("FutureStruct") + bytes(range(16)),
            "MapProperty": fstring("FutureKey") + fstring("FutureValue"),
            "ArrayProperty": fstring("FutureElement"),
            "EnumProperty": fstring("FutureEnum"),
            "ByteProperty": fstring("None"),
            "BoolProperty": b"\x01",
            "IntProperty": b"",
        }
        for kind, metadata in cases.items():
            with self.subTest(kind=kind):
                payload = b"" if kind == "BoolProperty" else b"\xde\xad\xbe\xef"
                self.roundtrip(record("Unrelated", kind, payload,
                                      metadata + b"\x01" + bytes(16)) + END)

    def test_nested_set_uses_compatible_nested_writer(self):
        nested = record("Nested", "SetProperty", struct.pack("<II", 0, 0),
                        fstring("StructProperty") + b"\x00") + END
        data = record("Wrapper", "StructProperty", nested,
                      fstring("TestStruct") + bytes(16) + b"\x00") + END
        self.roundtrip(data, path="")

    def test_rejects_unknown_framing(self):
        with self.assertRaisesRegex(SaveCompatibilityError, "Unsupported property framing"):
            self.roundtrip(record("New", "FutureProperty", b"payload") + END)

    def test_rejects_invalid_guid_flag(self):
        with self.assertRaisesRegex(SaveCompatibilityError, "GUID flag"):
            self.roundtrip(record("New", "SetProperty", bytes(8),
                                  fstring("StructProperty") + b"\x02") + END)

    def test_rejects_truncated_guid_and_payload(self):
        full = record("New", "SetProperty", bytes(32),
                      fstring("StructProperty") + b"\x01" + bytes(16))
        for truncated in (full[:-1], full[:-40]):
            with self.subTest(length=len(truncated)):
                with self.assertRaises(SaveCompatibilityError):
                    self.roundtrip(truncated)

    def test_does_not_patch_upstream_reader(self):
        data = record("New", "SetProperty", bytes(8), fstring("StructProperty") + b"\x00")
        with self.assertRaisesRegex(Exception, "Unknown type: SetProperty"):
            FArchiveReader(data + END).properties_until_end()


class TestSaveRoundtrip(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "Level.sav"
        self.raw = fixture()
        self.path.write_bytes(gc.compress_gvas_to_sav(self.raw, 0x32))

    def load(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return gc.load_save(self.path)[0]

    def test_real_binary_read_resize_write_and_readback(self):
        gvas = self.load()
        self.assertEqual(write_gvas(gvas), self.raw)
        world = gvas.properties["worldSaveData"]["value"]
        before = gc.survey(world)
        self.assertEqual(before[CHEST]["slotnum"], 54)
        gc.apply_slot_count(world, 358, container_filter=CHEST)
        output = self.path.with_name("resized.sav")
        gc.write_save(gvas, output)
        updated, _ = gc.decompress_sav_to_gvas(output.read_bytes())
        # Independently construct the expected bytes: exactly the chest capacity.
        marker = record("SlotNum", "IntProperty", struct.pack("<i", 54))
        self.assertEqual(self.raw.count(marker), 1)
        expected = self.raw.replace(marker, record("SlotNum", "IntProperty", struct.pack("<i", 358)))
        self.assertEqual(updated, expected)
        with contextlib.redirect_stdout(io.StringIO()):
            reloaded, _, _ = gc.load_save(output)
        after = gc.survey(reloaded.properties["worldSaveData"]["value"])
        self.assertEqual(after[CHEST]["items"], before[CHEST]["items"])
        self.assertEqual(after[CHEST]["slotnum"], 358)

    def test_save_without_set_still_roundtrips(self):
        raw = fixture(include_set=False)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(write_gvas(read_gvas(raw, gc.PALWORLD_TYPE_HINTS)), raw)

    def test_refuses_non_lossless_parse(self):
        duplicate = record("Repeated", "IntProperty", struct.pack("<i", 1))
        duplicate += record("Repeated", "IntProperty", struct.pack("<i", 2))
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SaveCompatibilityError, "Lossless save check"):
                read_gvas(fixture(extra_records=duplicate), gc.PALWORLD_TYPE_HINTS)

    def test_refuses_unrelated_changes_without_touching_output(self):
        for mutation in ("header", "trailer", "opaque", "item", "other_container"):
            for existing in (False, True):
                with self.subTest(mutation=mutation, existing=existing):
                    gvas = self.load()
                    world = gvas.properties["worldSaveData"]["value"]
                    gc.apply_slot_count(world, 358)
                    containers = gc.containers_by_id(world)
                    if mutation == "header":
                        gvas.header.engine_version_branch += "changed"
                    elif mutation == "trailer":
                        gvas.trailer += b"!"
                    elif mutation == "opaque":
                        world["UnrelatedMap"]["_preserved_body"] += b"!"
                    elif mutation == "item":
                        raw = containers[CHEST]["Slots"]["value"]["values"][0]["RawData"]["value"]
                        values = list(raw["values"])
                        values[4] = 99
                        raw["values"] = values
                    else:
                        containers[OTHER]["SlotNum"]["value"] = 21
                    output = self.path.with_name(f"{mutation}-{existing}.sav")
                    if existing:
                        output.write_bytes(b"do not truncate")
                    with self.assertRaisesRegex(SaveCompatibilityError, "non-capacity"):
                        gc.write_save(gvas, output)
                    self.assertEqual(containers[CHEST]["SlotNum"]["value"], 358)
                    if existing:
                        self.assertEqual(output.read_bytes(), b"do not truncate")
                    else:
                        self.assertFalse(output.exists())

    def test_capacity_restored_if_validation_serialization_raises(self):
        gvas = self.load()
        world = gvas.properties["worldSaveData"]["value"]
        gc.apply_slot_count(world, 358)
        with mock.patch.object(gc, "write_gvas", side_effect=[b"edited", ValueError("test")]):
            with self.assertRaisesRegex(ValueError, "test"):
                gc.write_save(gvas, self.path.with_name("unwritten.sav"))
        self.assertEqual(gc.containers_by_id(world)[CHEST]["SlotNum"]["value"], 358)


if __name__ == "__main__":
    unittest.main()
