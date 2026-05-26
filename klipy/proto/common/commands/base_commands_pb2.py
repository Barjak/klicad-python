"""Generated protocol buffer code."""
from google.protobuf.internal import builder as _builder
from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import symbol_database as _symbol_database
_sym_db = _symbol_database.Default()
from ...common.types import base_types_pb2 as common_dot_types_dot_base__types__pb2
DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n#common/commands/base_commands.proto\x12\x15kiapi.common.commands\x1a\x1dcommon/types/base_types.proto"\x0c\n\nGetVersion"G\n\x12GetVersionResponse\x121\n\x07version\x18\x01 \x01(\x0b2 .kiapi.common.types.KiCadVersion"\x06\n\x04Ping")\n\x12GetKiCadBinaryPath\x12\x13\n\x0bbinary_name\x18\x01 \x01(\t"\x1c\n\x0cPathResponse\x12\x0c\n\x04path\x18\x01 \x01(\t"8\n\x0eGetTextExtents\x12&\n\x04text\x18\x01 \x01(\x0b2\x18.kiapi.common.types.Text"r\n\rTextOrTextBox\x12(\n\x04text\x18\x01 \x01(\x0b2\x18.kiapi.common.types.TextH\x00\x12.\n\x07textbox\x18\x02 \x01(\x0b2\x1b.kiapi.common.types.TextBoxH\x00B\x07\n\x05inner"E\n\x0fGetTextAsShapes\x122\n\x04text\x18\x01 \x03(\x0b2$.kiapi.common.commands.TextOrTextBox"w\n\x0eTextWithShapes\x122\n\x04text\x18\x01 \x01(\x0b2$.kiapi.common.commands.TextOrTextBox\x121\n\x06shapes\x18\x02 \x01(\x0b2!.kiapi.common.types.CompoundShape"Z\n\x17GetTextAsShapesResponse\x12?\n\x10text_with_shapes\x18\x01 \x03(\x0b2%.kiapi.common.commands.TextWithShapes"+\n\x15GetPluginSettingsPath\x12\x12\n\nidentifier\x18\x01 \x01(\t""\n\x0eStringResponse\x12\x10\n\x08response\x18\x01 \x01(\t"\x19\n\tRunPython\x12\x0c\n\x04code\x18\x01 \x01(\t"q\n\x11RunPythonResponse\x12\n\n\x02ok\x18\x01 \x01(\x08\x12\x0e\n\x06stdout\x18\x02 \x01(\t\x12\x0e\n\x06stderr\x18\x03 \x01(\t\x12\x13\n\x0bresult_repr\x18\x04 \x01(\t\x12\x1b\n\x13exception_traceback\x18\x05 \x01(\tb\x06proto3')
_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, globals())
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'common.commands.base_commands_pb2', globals())
if _descriptor._USE_C_DESCRIPTORS == False:
    DESCRIPTOR._options = None
    _GETVERSION._serialized_start = 93
    _GETVERSION._serialized_end = 105
    _GETVERSIONRESPONSE._serialized_start = 107
    _GETVERSIONRESPONSE._serialized_end = 178
    _PING._serialized_start = 180
    _PING._serialized_end = 186
    _GETKICADBINARYPATH._serialized_start = 188
    _GETKICADBINARYPATH._serialized_end = 229
    _PATHRESPONSE._serialized_start = 231
    _PATHRESPONSE._serialized_end = 259
    _GETTEXTEXTENTS._serialized_start = 261
    _GETTEXTEXTENTS._serialized_end = 317
    _TEXTORTEXTBOX._serialized_start = 319
    _TEXTORTEXTBOX._serialized_end = 433
    _GETTEXTASSHAPES._serialized_start = 435
    _GETTEXTASSHAPES._serialized_end = 504
    _TEXTWITHSHAPES._serialized_start = 506
    _TEXTWITHSHAPES._serialized_end = 625
    _GETTEXTASSHAPESRESPONSE._serialized_start = 627
    _GETTEXTASSHAPESRESPONSE._serialized_end = 717
    _GETPLUGINSETTINGSPATH._serialized_start = 719
    _GETPLUGINSETTINGSPATH._serialized_end = 762
    _STRINGRESPONSE._serialized_start = 764
    _STRINGRESPONSE._serialized_end = 798
    _RUNPYTHON._serialized_start = 800
    _RUNPYTHON._serialized_end = 825
    _RUNPYTHONRESPONSE._serialized_start = 827
    _RUNPYTHONRESPONSE._serialized_end = 940