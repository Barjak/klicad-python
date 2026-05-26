"""Generated protocol buffer code."""
from google.protobuf.internal import builder as _builder
from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import symbol_database as _symbol_database
_sym_db = _symbol_database.Default()
from ...common.types import base_types_pb2 as common_dot_types_dot_base__types__pb2
from ...common.types import project_settings_pb2 as common_dot_types_dot_project__settings__pb2
DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n&common/commands/project_commands.proto\x12\x15kiapi.common.commands\x1a\x1dcommon/types/base_types.proto\x1a#common/types/project_settings.proto"\x0f\n\rGetNetClasses"I\n\x12NetClassesResponse\x123\n\x0bnet_classes\x18\x01 \x03(\x0b2\x1e.kiapi.common.project.NetClass"z\n\rSetNetClasses\x123\n\x0bnet_classes\x18\x01 \x03(\x0b2\x1e.kiapi.common.project.NetClass\x124\n\nmerge_mode\x18\x03 \x01(\x0e2 .kiapi.common.types.MapMergeMode"\\\n\x13ExpandTextVariables\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier\x12\x0c\n\x04text\x18\x02 \x03(\t"+\n\x1bExpandTextVariablesResponse\x12\x0c\n\x04text\x18\x01 \x03(\t"K\n\x10GetTextVariables\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier"\xb9\x01\n\x10SetTextVariables\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier\x126\n\tvariables\x18\x02 \x01(\x0b2#.kiapi.common.project.TextVariables\x124\n\nmerge_mode\x18\x03 \x01(\x0e2 .kiapi.common.types.MapMergeMode"L\n\x0cOpenDocument\x12.\n\x04type\x18\x01 \x01(\x0e2 .kiapi.common.types.DocumentType\x12\x0c\n\x04path\x18\x02 \x01(\t"O\n\x14OpenDocumentResponse\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier"H\n\rCloseDocument\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier"G\n\x0cSaveDocument\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifierb\x06proto3')
_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, globals())
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'common.commands.project_commands_pb2', globals())
if _descriptor._USE_C_DESCRIPTORS == False:
    DESCRIPTOR._options = None
    _GETNETCLASSES._serialized_start = 133
    _GETNETCLASSES._serialized_end = 148
    _NETCLASSESRESPONSE._serialized_start = 150
    _NETCLASSESRESPONSE._serialized_end = 223
    _SETNETCLASSES._serialized_start = 225
    _SETNETCLASSES._serialized_end = 347
    _EXPANDTEXTVARIABLES._serialized_start = 349
    _EXPANDTEXTVARIABLES._serialized_end = 441
    _EXPANDTEXTVARIABLESRESPONSE._serialized_start = 443
    _EXPANDTEXTVARIABLESRESPONSE._serialized_end = 486
    _GETTEXTVARIABLES._serialized_start = 488
    _GETTEXTVARIABLES._serialized_end = 563
    _SETTEXTVARIABLES._serialized_start = 566
    _SETTEXTVARIABLES._serialized_end = 751
    _OPENDOCUMENT._serialized_start = 753
    _OPENDOCUMENT._serialized_end = 829
    _OPENDOCUMENTRESPONSE._serialized_start = 831
    _OPENDOCUMENTRESPONSE._serialized_end = 910
    _CLOSEDOCUMENT._serialized_start = 912
    _CLOSEDOCUMENT._serialized_end = 984
    _SAVEDOCUMENT._serialized_start = 986
    _SAVEDOCUMENT._serialized_end = 1057