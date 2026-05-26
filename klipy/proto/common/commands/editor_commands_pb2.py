"""Generated protocol buffer code."""
from google.protobuf.internal import builder as _builder
from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import symbol_database as _symbol_database
_sym_db = _symbol_database.Default()
from google.protobuf import any_pb2 as google_dot_protobuf_dot_any__pb2
from ...common.types import base_types_pb2 as common_dot_types_dot_base__types__pb2
from ...common.types import enums_pb2 as common_dot_types_dot_enums__pb2
DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n%common/commands/editor_commands.proto\x12\x15kiapi.common.commands\x1a\x19google/protobuf/any.proto\x1a\x1dcommon/types/base_types.proto\x1a\x18common/types/enums.proto"=\n\rRefreshEditor\x12,\n\x05frame\x18\x01 \x01(\x0e2\x1d.kiapi.common.types.FrameType"B\n\x10GetOpenDocuments\x12.\n\x04type\x18\x01 \x01(\x0e2 .kiapi.common.types.DocumentType"T\n\x18GetOpenDocumentsResponse\x128\n\tdocuments\x18\x01 \x03(\x0b2%.kiapi.common.types.DocumentSpecifier"9\n\x0bSaveOptions\x12\x11\n\toverwrite\x18\x01 \x01(\x08\x12\x17\n\x0finclude_project\x18\x02 \x01(\x08"\x90\x01\n\x12SaveCopyOfDocument\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier\x12\x0c\n\x04path\x18\x02 \x01(\t\x123\n\x07options\x18\x03 \x01(\x0b2".kiapi.common.commands.SaveOptions"I\n\x0eRevertDocument\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier"\x1b\n\tRunAction\x12\x0e\n\x06action\x18\x01 \x01(\t"K\n\x11RunActionResponse\x126\n\x06status\x18\x01 \x01(\x0e2&.kiapi.common.commands.RunActionStatus"=\n\x0bBeginCommit\x12.\n\x06header\x18\x01 \x01(\x0b2\x1e.kiapi.common.types.ItemHeader";\n\x13BeginCommitResponse\x12$\n\x02id\x18\x01 \x01(\x0b2\x18.kiapi.common.types.KIID"\xa7\x01\n\tEndCommit\x12$\n\x02id\x18\x01 \x01(\x0b2\x18.kiapi.common.types.KIID\x123\n\x06action\x18\x02 \x01(\x0e2#.kiapi.common.commands.CommitAction\x12\x0f\n\x07message\x18\x03 \x01(\t\x12.\n\x06header\x18\x04 \x01(\x0b2\x1e.kiapi.common.types.ItemHeader"\x13\n\x11EndCommitResponse"\x8f\x01\n\x0bCreateItems\x12.\n\x06header\x18\x01 \x01(\x0b2\x1e.kiapi.common.types.ItemHeader\x12#\n\x05items\x18\x02 \x03(\x0b2\x14.google.protobuf.Any\x12+\n\tcontainer\x18\x03 \x01(\x0b2\x18.kiapi.common.types.KIID"X\n\nItemStatus\x123\n\x04code\x18\x01 \x01(\x0e2%.kiapi.common.commands.ItemStatusCode\x12\x15\n\rerror_message\x18\x02 \x01(\t"k\n\x12ItemCreationResult\x121\n\x06status\x18\x01 \x01(\x0b2!.kiapi.common.commands.ItemStatus\x12"\n\x04item\x18\x02 \x01(\x0b2\x14.google.protobuf.Any"\xbe\x01\n\x13CreateItemsResponse\x12.\n\x06header\x18\x01 \x01(\x0b2\x1e.kiapi.common.types.ItemHeader\x125\n\x06status\x18\x02 \x01(\x0e2%.kiapi.common.types.ItemRequestStatus\x12@\n\rcreated_items\x18\x03 \x03(\x0b2).kiapi.common.commands.ItemCreationResult"n\n\x08GetItems\x12.\n\x06header\x18\x01 \x01(\x0b2\x1e.kiapi.common.types.ItemHeader\x122\n\x05types\x18\x02 \x03(\x0e2#.kiapi.common.types.KiCadObjectType"g\n\x0cGetItemsById\x12.\n\x06header\x18\x01 \x01(\x0b2\x1e.kiapi.common.types.ItemHeader\x12\'\n\x05items\x18\x02 \x03(\x0b2\x18.kiapi.common.types.KIID"\x9e\x01\n\x10GetItemsResponse\x12.\n\x06header\x18\x01 \x01(\x0b2\x1e.kiapi.common.types.ItemHeader\x125\n\x06status\x18\x02 \x01(\x0e2%.kiapi.common.types.ItemRequestStatus\x12#\n\x05items\x18\x03 \x03(\x0b2\x14.google.protobuf.Any"b\n\x0bUpdateItems\x12.\n\x06header\x18\x01 \x01(\x0b2\x1e.kiapi.common.types.ItemHeader\x12#\n\x05items\x18\x02 \x03(\x0b2\x14.google.protobuf.Any"i\n\x10ItemUpdateResult\x121\n\x06status\x18\x01 \x01(\x0b2!.kiapi.common.commands.ItemStatus\x12"\n\x04item\x18\x02 \x01(\x0b2\x14.google.protobuf.Any"\xbc\x01\n\x13UpdateItemsResponse\x12.\n\x06header\x18\x01 \x01(\x0b2\x1e.kiapi.common.types.ItemHeader\x125\n\x06status\x18\x02 \x01(\x0e2%.kiapi.common.types.ItemRequestStatus\x12>\n\rupdated_items\x18\x03 \x03(\x0b2\'.kiapi.common.commands.ItemUpdateResult"i\n\x0bDeleteItems\x12.\n\x06header\x18\x01 \x01(\x0b2\x1e.kiapi.common.types.ItemHeader\x12*\n\x08item_ids\x18\x02 \x03(\x0b2\x18.kiapi.common.types.KIID"u\n\x12ItemDeletionResult\x12$\n\x02id\x18\x01 \x01(\x0b2\x18.kiapi.common.types.KIID\x129\n\x06status\x18\x02 \x01(\x0e2).kiapi.common.commands.ItemDeletionStatus"\xbe\x01\n\x13DeleteItemsResponse\x12.\n\x06header\x18\x01 \x01(\x0b2\x1e.kiapi.common.types.ItemHeader\x125\n\x06status\x18\x02 \x01(\x0e2%.kiapi.common.types.ItemRequestStatus\x12@\n\rdeleted_items\x18\x03 \x03(\x0b2).kiapi.common.commands.ItemDeletionResult"\x9f\x01\n\x0eGetBoundingBox\x12.\n\x06header\x18\x01 \x01(\x0b2\x1e.kiapi.common.types.ItemHeader\x12\'\n\x05items\x18\x02 \x03(\x0b2\x18.kiapi.common.types.KIID\x124\n\x04mode\x18\x03 \x01(\x0e2&.kiapi.common.commands.BoundingBoxMode"j\n\x16GetBoundingBoxResponse\x12\'\n\x05items\x18\x01 \x03(\x0b2\x18.kiapi.common.types.KIID\x12\'\n\x05boxes\x18\x02 \x03(\x0b2\x18.kiapi.common.types.Box2"r\n\x0cGetSelection\x12.\n\x06header\x18\x01 \x01(\x0b2\x1e.kiapi.common.types.ItemHeader\x122\n\x05types\x18\x02 \x03(\x0e2#.kiapi.common.types.KiCadObjectType"8\n\x11SelectionResponse\x12#\n\x05items\x18\x01 \x03(\x0b2\x14.google.protobuf.Any"i\n\x0eAddToSelection\x12.\n\x06header\x18\x01 \x01(\x0b2\x1e.kiapi.common.types.ItemHeader\x12\'\n\x05items\x18\x02 \x03(\x0b2\x18.kiapi.common.types.KIID"n\n\x13RemoveFromSelection\x12.\n\x06header\x18\x01 \x01(\x0b2\x1e.kiapi.common.types.ItemHeader\x12\'\n\x05items\x18\x02 \x03(\x0b2\x18.kiapi.common.types.KIID"@\n\x0eClearSelection\x12.\n\x06header\x18\x01 \x01(\x0b2\x1e.kiapi.common.types.ItemHeader"\xa1\x01\n\x07HitTest\x12.\n\x06header\x18\x01 \x01(\x0b2\x1e.kiapi.common.types.ItemHeader\x12$\n\x02id\x18\x02 \x01(\x0b2\x18.kiapi.common.types.KIID\x12-\n\x08position\x18\x03 \x01(\x0b2\x1b.kiapi.common.types.Vector2\x12\x11\n\ttolerance\x18\x04 \x01(\x05"G\n\x0fHitTestResponse\x124\n\x06result\x18\x01 \x01(\x0e2$.kiapi.common.commands.HitTestResult"L\n\x11GetTitleBlockInfo\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier"\x85\x01\n\x11SetTitleBlockInfo\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier\x127\n\x0btitle_block\x18\x02 \x01(\x0b2".kiapi.common.types.TitleBlockInfo"J\n\x0fGetPageSettings\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier"\x83\x01\n\x0fSetPageSettings\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier\x127\n\rpage_settings\x18\x02 \x01(\x0b2 .kiapi.common.types.PageSettings"O\n\x14SaveDocumentToString\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier"b\n\x15SavedDocumentResponse\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier\x12\x10\n\x08contents\x18\x02 \x01(\t"\x17\n\x15SaveSelectionToString"Q\n\x16SavedSelectionResponse\x12%\n\x03ids\x18\x01 \x03(\x0b2\x18.kiapi.common.types.KIID\x12\x10\n\x08contents\x18\x02 \x01(\t"j\n\x1dParseAndCreateItemsFromString\x127\n\x08document\x18\x01 \x01(\x0b2%.kiapi.common.types.DocumentSpecifier\x12\x10\n\x08contents\x18\x02 \x01(\t*W\n\x0fRunActionStatus\x12\x0f\n\x0bRAS_UNKNOWN\x10\x00\x12\n\n\x06RAS_OK\x10\x01\x12\x0f\n\x0bRAS_INVALID\x10\x02\x12\x16\n\x12RAS_FRAME_NOT_OPEN\x10\x03*=\n\x0cCommitAction\x12\x0f\n\x0bCMA_UNKNOWN\x10\x00\x12\x0e\n\nCMA_COMMIT\x10\x01\x12\x0c\n\x08CMA_DROP\x10\x02*\x93\x01\n\x0eItemStatusCode\x12\x0f\n\x0bISC_UNKNOWN\x10\x00\x12\n\n\x06ISC_OK\x10\x01\x12\x14\n\x10ISC_INVALID_TYPE\x10\x02\x12\x10\n\x0cISC_EXISTING\x10\x03\x12\x13\n\x0fISC_NONEXISTENT\x10\x04\x12\x11\n\rISC_IMMUTABLE\x10\x05\x12\x14\n\x10ISC_INVALID_DATA\x10\x07*Y\n\x12ItemDeletionStatus\x12\x0f\n\x0bIDS_UNKNOWN\x10\x00\x12\n\n\x06IDS_OK\x10\x01\x12\x13\n\x0fIDS_NONEXISTENT\x10\x02\x12\x11\n\rIDS_IMMUTABLE\x10\x03*R\n\x0fBoundingBoxMode\x12\x0f\n\x0bBBM_UNKNOWN\x10\x00\x12\x11\n\rBBM_ITEM_ONLY\x10\x01\x12\x1b\n\x17BBM_ITEM_AND_CHILD_TEXT\x10\x02*=\n\rHitTestResult\x12\x0f\n\x0bHTR_UNKNOWN\x10\x00\x12\x0e\n\nHTR_NO_HIT\x10\x01\x12\x0b\n\x07HTR_HIT\x10\x02b\x06proto3')
_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, globals())
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'common.commands.editor_commands_pb2', globals())
if _descriptor._USE_C_DESCRIPTORS == False:
    DESCRIPTOR._options = None
    _RUNACTIONSTATUS._serialized_start = 4587
    _RUNACTIONSTATUS._serialized_end = 4674
    _COMMITACTION._serialized_start = 4676
    _COMMITACTION._serialized_end = 4737
    _ITEMSTATUSCODE._serialized_start = 4740
    _ITEMSTATUSCODE._serialized_end = 4887
    _ITEMDELETIONSTATUS._serialized_start = 4889
    _ITEMDELETIONSTATUS._serialized_end = 4978
    _BOUNDINGBOXMODE._serialized_start = 4980
    _BOUNDINGBOXMODE._serialized_end = 5062
    _HITTESTRESULT._serialized_start = 5064
    _HITTESTRESULT._serialized_end = 5125
    _REFRESHEDITOR._serialized_start = 148
    _REFRESHEDITOR._serialized_end = 209
    _GETOPENDOCUMENTS._serialized_start = 211
    _GETOPENDOCUMENTS._serialized_end = 277
    _GETOPENDOCUMENTSRESPONSE._serialized_start = 279
    _GETOPENDOCUMENTSRESPONSE._serialized_end = 363
    _SAVEOPTIONS._serialized_start = 365
    _SAVEOPTIONS._serialized_end = 422
    _SAVECOPYOFDOCUMENT._serialized_start = 425
    _SAVECOPYOFDOCUMENT._serialized_end = 569
    _REVERTDOCUMENT._serialized_start = 571
    _REVERTDOCUMENT._serialized_end = 644
    _RUNACTION._serialized_start = 646
    _RUNACTION._serialized_end = 673
    _RUNACTIONRESPONSE._serialized_start = 675
    _RUNACTIONRESPONSE._serialized_end = 750
    _BEGINCOMMIT._serialized_start = 752
    _BEGINCOMMIT._serialized_end = 813
    _BEGINCOMMITRESPONSE._serialized_start = 815
    _BEGINCOMMITRESPONSE._serialized_end = 874
    _ENDCOMMIT._serialized_start = 877
    _ENDCOMMIT._serialized_end = 1044
    _ENDCOMMITRESPONSE._serialized_start = 1046
    _ENDCOMMITRESPONSE._serialized_end = 1065
    _CREATEITEMS._serialized_start = 1068
    _CREATEITEMS._serialized_end = 1211
    _ITEMSTATUS._serialized_start = 1213
    _ITEMSTATUS._serialized_end = 1301
    _ITEMCREATIONRESULT._serialized_start = 1303
    _ITEMCREATIONRESULT._serialized_end = 1410
    _CREATEITEMSRESPONSE._serialized_start = 1413
    _CREATEITEMSRESPONSE._serialized_end = 1603
    _GETITEMS._serialized_start = 1605
    _GETITEMS._serialized_end = 1715
    _GETITEMSBYID._serialized_start = 1717
    _GETITEMSBYID._serialized_end = 1820
    _GETITEMSRESPONSE._serialized_start = 1823
    _GETITEMSRESPONSE._serialized_end = 1981
    _UPDATEITEMS._serialized_start = 1983
    _UPDATEITEMS._serialized_end = 2081
    _ITEMUPDATERESULT._serialized_start = 2083
    _ITEMUPDATERESULT._serialized_end = 2188
    _UPDATEITEMSRESPONSE._serialized_start = 2191
    _UPDATEITEMSRESPONSE._serialized_end = 2379
    _DELETEITEMS._serialized_start = 2381
    _DELETEITEMS._serialized_end = 2486
    _ITEMDELETIONRESULT._serialized_start = 2488
    _ITEMDELETIONRESULT._serialized_end = 2605
    _DELETEITEMSRESPONSE._serialized_start = 2608
    _DELETEITEMSRESPONSE._serialized_end = 2798
    _GETBOUNDINGBOX._serialized_start = 2801
    _GETBOUNDINGBOX._serialized_end = 2960
    _GETBOUNDINGBOXRESPONSE._serialized_start = 2962
    _GETBOUNDINGBOXRESPONSE._serialized_end = 3068
    _GETSELECTION._serialized_start = 3070
    _GETSELECTION._serialized_end = 3184
    _SELECTIONRESPONSE._serialized_start = 3186
    _SELECTIONRESPONSE._serialized_end = 3242
    _ADDTOSELECTION._serialized_start = 3244
    _ADDTOSELECTION._serialized_end = 3349
    _REMOVEFROMSELECTION._serialized_start = 3351
    _REMOVEFROMSELECTION._serialized_end = 3461
    _CLEARSELECTION._serialized_start = 3463
    _CLEARSELECTION._serialized_end = 3527
    _HITTEST._serialized_start = 3530
    _HITTEST._serialized_end = 3691
    _HITTESTRESPONSE._serialized_start = 3693
    _HITTESTRESPONSE._serialized_end = 3764
    _GETTITLEBLOCKINFO._serialized_start = 3766
    _GETTITLEBLOCKINFO._serialized_end = 3842
    _SETTITLEBLOCKINFO._serialized_start = 3845
    _SETTITLEBLOCKINFO._serialized_end = 3978
    _GETPAGESETTINGS._serialized_start = 3980
    _GETPAGESETTINGS._serialized_end = 4054
    _SETPAGESETTINGS._serialized_start = 4057
    _SETPAGESETTINGS._serialized_end = 4188
    _SAVEDOCUMENTTOSTRING._serialized_start = 4190
    _SAVEDOCUMENTTOSTRING._serialized_end = 4269
    _SAVEDDOCUMENTRESPONSE._serialized_start = 4271
    _SAVEDDOCUMENTRESPONSE._serialized_end = 4369
    _SAVESELECTIONTOSTRING._serialized_start = 4371
    _SAVESELECTIONTOSTRING._serialized_end = 4394
    _SAVEDSELECTIONRESPONSE._serialized_start = 4396
    _SAVEDSELECTIONRESPONSE._serialized_end = 4477
    _PARSEANDCREATEITEMSFROMSTRING._serialized_start = 4479
    _PARSEANDCREATEITEMSFROMSTRING._serialized_end = 4585