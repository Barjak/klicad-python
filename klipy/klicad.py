# Copyright The KliCAD Developers
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the “Software”), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Classes for interacting with KliCAD at a high level"""

import os
import platform
import random
import string
from dataclasses import dataclass
from tempfile import gettempdir
from typing import Optional, Sequence, Union


@dataclass
class RunPythonResult:
    """Structured return value of KliCAD.run_python (local fork addition).

    Mirrors the RunPythonResponse protobuf. ``ok`` is True iff the embedded
    interpreter ran the code without an uncaught exception; on False, see
    ``exception_traceback`` for the trace.
    """
    ok: bool
    stdout: str
    stderr: str
    result_repr: str
    exception_traceback: str

    def __bool__(self) -> bool:
        return self.ok
from google.protobuf.empty_pb2 import Empty

from klipy.board import Board
from klipy.client import KiCadClient, ApiError
from klipy.common_types import Text, TextBox, CompoundShape
from klipy.errors import FutureVersionError
from klipy.geometry import Box2
from klipy.project import Project
from klipy.schematic import Schematic
from klipy.server import KliCADServer, find_kicad_cli
from klipy.proto.common import commands
from klipy.proto.common.types import base_types_pb2, DocumentType, DocumentSpecifier
from klipy.proto.common.commands import base_commands_pb2, project_commands_pb2
from klipy.klicad_api_version import KLICAD_API_VERSION


def _default_socket_path() -> str:
    path = os.environ.get('KLICAD_API_SOCKET')
    if path is not None:
        return path
    if platform.system() == 'Windows':
        return f'ipc://{gettempdir()}\\klicad\\api.sock'
    else:
        # Check for default socket path of KliCAD flatpak on flathub
        home = os.environ.get('HOME')
        if home is not None:
            flatpak_socket_path = f'{home}/.var/app/org.kicad.KliCAD/cache/tmp/klicad/api.sock'
            if os.path.exists(flatpak_socket_path):
                return f'ipc://{flatpak_socket_path}'

        return 'ipc:///tmp/klicad/api.sock'

def _random_client_name() -> str:
    return 'anonymous-'+''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

def _default_kicad_token() -> str:
    token = os.environ.get('KLICAD_API_TOKEN')
    if token is not None:
        return token
    return ""

class KliCADVersion:
    def __init__(self, major: int, minor: int, patch: int, full_version: str):
        self.major = major
        self.minor = minor
        self.patch = patch
        self.full_version = full_version

    @staticmethod
    def from_proto(proto: base_types_pb2.KliCADVersion) -> 'KliCADVersion':
        return KliCADVersion(proto.major, proto.minor, proto.patch, proto.full_version)

    @staticmethod
    def from_git_describe(describe: str) -> 'KliCADVersion':
        parts = describe.split('-')
        version_part = parts[0]

        try:
            major, minor, patch = map(int, version_part.split('.'))
        except ValueError:
            return KliCADVersion(0, 0, 0, describe)

        if len(parts) > 1:
            additional_info = '-'.join(parts[1:])
            return KliCADVersion(major, minor, patch, f"{version_part}-{additional_info}")

        return KliCADVersion(major, minor, patch, f"{version_part}")

    def __repr__(self):
        return f"{self.major}.{self.minor}.{self.patch} ({self.full_version})"

    def __eq__(self, other):
        if not isinstance(other, KliCADVersion):
            return NotImplemented

        return (
            (self.major, self.minor, self.patch) == (other.major, other.minor, other.patch)
            )

    def __lt__(self, other):
        if not isinstance(other, KliCADVersion):
            return NotImplemented
        return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)

    def __le__(self, other):
        return self == other or self < other

    def __gt__(self, other):
        return not self <= other

    def __ge__(self, other):
        return not self < other

class KliCAD:
    def __init__(self, socket_path: Optional[str]=None,
                 client_name: Optional[str]=None,
                 kicad_token: Optional[str]=None,
                 timeout_ms: int=2000,
                 headless: bool=False,
                 kicad_cli_path: Optional[str]=None,
                 file_path: Optional[str]=None):
        """Creates a connection to a running KliCAD instance

        :param socket_path: The path to the IPC API socket (leave default to read from the
            KLICAD_API_SOCKET environment variable, which will be set automatically by KliCAD when
            launching API plugins, or to use the default platform-dependent socket path if the
            environment variable is not set).
        :param client_name: A unique name identifying this plugin instance.  Leave default to
            generate a random client name.
        :param kicad_token: A token that can be provided to the client to uniquely identify a
            KliCAD instance.  Leave default to read from the KLICAD_API_TOKEN environment variable.
        :param timeout_ms: The maximum time to wait for a response from KliCAD, in milliseconds
        :param headless: Start and connect to a headless `kicad-cli api-server` instance.
        :param kicad_cli_path: Optional path to `kicad-cli`.
        :param file_path: Optional path to a board, schematic, or project file to pre-load in headless mode.
        """
        self._server: Optional[KliCADServer] = None

        if headless:
            if socket_path is not None:
                raise ValueError("socket_path cannot be used when headless=True")

            cli_path = find_kicad_cli(kicad_cli_path)
            server = KliCADServer(cli_path, file_path=file_path)
            server.start()
            server.wait_for_ready(timeout_s=max(float(timeout_ms) / 1000.0, 5.0))
            self._server = server
            socket_path = server.socket_url

        if socket_path is None:
            socket_path = _default_socket_path()
        if client_name is None:
            client_name = _random_client_name()
        if kicad_token is None:
            kicad_token = _default_kicad_token()

        try:
            self._client = KiCadClient(socket_path, client_name, kicad_token, timeout_ms)
        except Exception:
            if self._server is not None:
                self._server.stop()
                self._server = None
            raise

    @staticmethod
    def from_client(client: KiCadClient):
        """Creates a KliCAD object from an existing KliCAD client"""
        k = KliCAD.__new__(KliCAD)
        k._client = client
        k._server = None
        return k

    def close(self):
        """Close the KliCAD connection and stop any headless server started by this object."""
        if hasattr(self, '_client'):
            self._client.close()

        if self._server is not None:
            self._server.stop()
            self._server = None

    def __enter__(self) -> 'KliCAD':
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def get_version(self) -> KliCADVersion:
        """Returns the KliCAD version as a string, including any package-specific info"""
        response = self._client.send(commands.GetVersion(), commands.GetVersionResponse)
        return KliCADVersion.from_proto(response.version)

    def get_api_version(self) -> KliCADVersion:
        """Returns the version of KliCAD that this library was built against"""
        return KliCADVersion.from_git_describe(KLICAD_API_VERSION)

    def check_version(self) -> bool:
        """Checks if the connected KliCAD version matches the version this library was built against"""
        kicad_version = self.get_version()
        api_version = self.get_api_version()

        if kicad_version > api_version:
            raise FutureVersionError(
                f"Warning: Connected KliCAD version ({kicad_version}) is newer than "
                f"the API version of kicad-python ({api_version})"
            )

        return True

    def ping(self):
        self._client.send(commands.Ping(), Empty)

    def is_alive(self) -> bool:
        """Cheap probe: True if KliCAD's IPC API responds to a Ping; False otherwise.

        Use to branch flow up-front when KliCAD may not be running.  Unlike
        ``ping()``, this swallows any IPC / connection errors and returns
        False instead of raising — appropriate for ``if k.is_alive(): …``
        guards.  Does not wait on slow operations; only confirms the
        round-trip works.
        """
        try:
            self._client.send(commands.Ping(), Empty)
            return True
        except Exception:
            return False

    def run_python(self, code: str) -> "RunPythonResult":
        """Run Python source in KliCAD's embedded interpreter (local fork).

        The interpreter is always-on, lives in the KliCAD process, and exposes
        the kicad_native pybind11 module that wraps KliCAD's C++ surface.
        State (variables in __main__) persists across calls.

        If the code ends in an expression, its repr() is returned in
        ``result_repr``; otherwise that field is empty.
        """
        req = base_commands_pb2.RunPython()
        req.code = code
        resp = self._client.send(req, base_commands_pb2.RunPythonResponse)
        return RunPythonResult(
            ok=resp.ok,
            stdout=resp.stdout,
            stderr=resp.stderr,
            result_repr=resp.result_repr,
            exception_traceback=resp.exception_traceback,
        )

    def get_kicad_binary_path(self, binary_name: str) -> str:
        """Returns the full path to the given KliCAD binary

        :param binary_name: The short name of the binary, such as `kicad-cli` or `kicad-cli.exe`.
                            If on Windows, an `.exe` extension will be assumed if not present.
        :return: The full path to the binary
        """
        cmd = commands.GetKiCadBinaryPath()
        cmd.binary_name = binary_name
        return self._client.send(cmd, commands.PathResponse).path

    def get_plugin_settings_path(self, identifier: str) -> str:
        """Return a writeable path that a plugin can use for storing persistent data such as
        configuration files, etc.  This path may not yet exist; actual creation of the directory
        for a given plugin is up to the plugin itself.  Files in this path will not be modified if
        the plugin is uninstalled or upgraded.

        :param identifier: should be the full identifier of the plugin (e.g. org.kicad.myplugin)
        :return: a path, with local separators, that the plugin can use for storing settings
        """
        cmd = commands.GetPluginSettingsPath()
        cmd.identifier = identifier
        return self._client.send(cmd, commands.StringResponse).response

    def run_action(self, action: str):
        """Runs a KliCAD tool action, if it is available

        WARNING: This is an unstable API and is not intended for use other
        than by API developers. KliCAD does not guarantee the stability of
        action names, and running actions may have unintended side effects.
        :param action: the name of a KliCAD TOOL_ACTION
        :return: a value from the KIAPI.COMMON.COMMANDS.RUN_ACTION_STATUS enum
        """
        command = commands.RunAction()
        command.action = action
        return self._client.send(command, commands.RunActionResponse)

    def get_open_documents(self, doc_type: DocumentType.ValueType) -> Sequence[DocumentSpecifier]:
        """Retrieves a list of open documents matching the given type"""
        command = commands.GetOpenDocuments()
        command.type = doc_type
        response = self._client.send(command, commands.GetOpenDocumentsResponse)
        return response.documents

    def open_document(self, path: str, type: DocumentType.ValueType) -> DocumentSpecifier:
        """In headless mode, opens a document.  Not currently supported for GUI mode.

        .. versionadded:: 0.7.0
        """
        command = project_commands_pb2.OpenDocument()
        command.path = path
        command.type = type
        response = self._client.send(command, project_commands_pb2.OpenDocumentResponse)
        return response.document

    def close_document(self, document: DocumentSpecifier):
        """In headless mode, closes an open document.  Not currently supported for GUI mode.

        .. versionadded:: 0.7.0
        """
        command = project_commands_pb2.CloseDocument()
        command.document.CopyFrom(document)
        self._client.send(command, Empty)

    def get_project(self, document: DocumentSpecifier) -> Project:
        """Returns a Project object for the given document"""
        return Project(self._client, document)

    def get_board(self) -> Board:
        """Retrieves a reference to the PCB open in KliCAD, if one exists"""
        docs = self.get_open_documents(DocumentType.DOCTYPE_PCB)
        if len(docs) == 0:
            raise ApiError("Expected to be able to retrieve at least one board")
        return Board(self._client, docs[0])

    def get_schematic(self) -> Schematic:
        """
        .. versionadded:: 0.x.y (KliCAD 11)
        """
        docs = self.get_open_documents(DocumentType.DOCTYPE_SCHEMATIC)
        if len(docs) == 0:
            raise ApiError("Expected to be able to retrieve at least one schematic")
        return Schematic(self._client, docs[0])

    # Utility functions

    def get_text_extents(self, text: Text) -> Box2:
        """Returns the bounding box of the given text object"""
        cmd = base_commands_pb2.GetTextExtents()
        cmd.text.CopyFrom(text.proto)
        reply = self._client.send(cmd, base_types_pb2.Box2)
        return Box2.from_proto(reply)

    def get_text_as_shapes(
        self, texts: Union[Text, TextBox, Sequence[Union[Text, TextBox]]]
    ) -> list[CompoundShape]:
        """Returns polygonal shapes representing the given text objects"""
        if isinstance(texts, Text) or isinstance(texts, TextBox):
            texts = [texts]

        cmd = base_commands_pb2.GetTextAsShapes()
        for t in texts:
            inner = base_commands_pb2.TextOrTextBox()
            if isinstance(t, Text):
                inner.text.CopyFrom(t.proto)
            else:
                inner.textbox.CopyFrom(t.proto)
            cmd.text.append(inner)

        reply = self._client.send(cmd, base_commands_pb2.GetTextAsShapesResponse)

        return [CompoundShape(entry.shapes) for entry in reply.text_with_shapes]
