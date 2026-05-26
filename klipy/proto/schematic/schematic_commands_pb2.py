"""Generated protocol buffer code."""
from google.protobuf.internal import builder as _builder
from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import symbol_database as _symbol_database
_sym_db = _symbol_database.Default()
from ..common.types import base_types_pb2 as common_dot_types_dot_base__types__pb2
from ..common.types import enums_pb2 as common_dot_types_dot_enums__pb2
from ..schematic import schematic_types_pb2 as schematic_dot_schematic__types__pb2
DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n"schematic/schematic_commands.proto\x12\x15kiapi.schematic.types\x1a\x1dcommon/types/base_types.proto\x1a\x18common/types/enums.proto\x1a\x1fschematic/schematic_types.proto"P\n\x15GetSchematicHierarchy\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier"\x95\x01\n\x1aSchematicHierarchyResponse\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier\x12>\n\x10top_level_sheets\x18\x02 \x03(\x0b2$.kiapi.schematic.types.SheetInstance"\x82\x01\n\x13GetSchematicNetlist\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier\x122\n\x05types\x18\x02 \x03(\x0e2#.kiapi.common.types.KiCadObjectType"\x86\x01\n\x18SchematicNetlistResponse\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier\x121\n\x04nets\x18\x02 \x03(\x0b2#.kiapi.schematic.types.SchematicNetb\x06proto3')
_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, globals())
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'schematic.schematic_commands_pb2', globals())
if _descriptor._USE_C_DESCRIPTORS == False:
    DESCRIPTOR._options = None
    _GETSCHEMATICHIERARCHY._serialized_start = 151
    _GETSCHEMATICHIERARCHY._serialized_end = 231
    _SCHEMATICHIERARCHYRESPONSE._serialized_start = 234
    _SCHEMATICHIERARCHYRESPONSE._serialized_end = 383
    _GETSCHEMATICNETLIST._serialized_start = 386
    _GETSCHEMATICNETLIST._serialized_end = 516
    _SCHEMATICNETLISTRESPONSE._serialized_start = 519
    _SCHEMATICNETLISTRESPONSE._serialized_end = 653