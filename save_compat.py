"""Read chest data without interpreting unrelated, version-dependent world data.

The pinned library does not support SetProperty and its custom RawData decoders
assume older Palworld layouts. Keep unrelated world properties and sets opaque,
including their original headers. Only known property framing is accepted.
These reader/writer subclasses are local to this tool; the library is not patched.
"""

from palworld_save_tools.archive import FArchiveReader, FArchiveWriter
from palworld_save_tools.gvas import GvasFile, GvasHeader


CHEST_FIELDS = {
    ".worldSaveData.GuildExtraSaveDataMap",
    ".worldSaveData.ItemContainerSaveData",
}
PRIMITIVE_TYPES = {
    "IntProperty", "Int8Property", "Int16Property", "Int64Property",
    "UInt16Property", "UInt32Property", "UInt64Property",
    "FixedPoint64Property", "FloatProperty", "DoubleProperty",
    "StrProperty", "NameProperty",
}


class SaveCompatibilityError(ValueError):
    """The save cannot be edited without risking unrelated data."""


class ChestReader(FArchiveReader):
    def _read_exact(self, size, path):
        data = self.read(size)
        if len(data) != size:
            raise SaveCompatibilityError(f"Truncated property: {path}")
        return data

    def property(self, type_name, size, path, nested_caller_path=""):
        world_field = path.startswith(".worldSaveData.") and path.count(".") == 2
        preserve = (world_field and path not in CHEST_FIELDS) or type_name == "SetProperty"
        if not preserve:
            return super().property(type_name, size, path, nested_caller_path)

        start = self.data.tell()
        # The declared size excludes type-specific metadata and the optional GUID.
        if type_name == "StructProperty":
            self.fstring()
            self._read_exact(16, path)
        elif type_name == "MapProperty":
            self.fstring()
            self.fstring()
        elif type_name in {"ArrayProperty", "SetProperty", "EnumProperty", "ByteProperty"}:
            self.fstring()
        elif type_name == "BoolProperty":
            self._read_exact(1, path)
        elif type_name not in PRIMITIVE_TYPES:
            raise SaveCompatibilityError(f"Unsupported property framing: {type_name} ({path})")

        flag = self._read_exact(1, path)
        if flag not in (b"\x00", b"\x01"):
            raise SaveCompatibilityError(f"Invalid property GUID flag: {path}")
        if flag == b"\x01":
            self._read_exact(16, path)
        end = self.data.tell() + size
        if size < 0 or end > self.size:
            raise SaveCompatibilityError(f"Property exceeds save boundary: {path}")
        self.data.seek(start)
        return {
            "type": type_name,
            "_preserved_body": self._read_exact(end - start, path),
            "_preserved_size": size,
        }


class ChestWriter(FArchiveWriter):
    def copy(self):
        # The base implementation creates FArchiveWriter, losing opaque support
        # inside nested structs, arrays, and maps.
        return type(self)(self.custom_properties, debug=self.debug)

    def property_inner(self, property_type, prop):
        if "_preserved_body" in prop:
            self.write(prop["_preserved_body"])
            return prop["_preserved_size"]
        return super().property_inner(property_type, prop)


def write_gvas(gvas):
    writer = ChestWriter()
    gvas.header.write(writer)
    writer.properties(gvas.properties)
    writer.write(gvas.trailer)
    return writer.bytes()


def read_gvas(data, type_hints):
    gvas = GvasFile()
    # No custom RawData decoders: the chest helpers consume their byte arrays.
    with ChestReader(data, type_hints=type_hints) as reader:
        gvas.header = GvasHeader.read(reader)
        gvas.properties = reader.properties_until_end()
        gvas.trailer = reader.read_to_end()
    if write_gvas(gvas) != data:
        raise SaveCompatibilityError("Lossless save check failed; refusing to edit this save")
    return gvas
