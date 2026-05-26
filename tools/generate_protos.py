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

import argparse
import fnmatch
import os
import platform
import subprocess

_default_protoc = "protoc.exe" if platform.system() == "Windows" else "protoc"
_default_protol = "protol.exe" if platform.system() == "Windows" else "protol"

def generate_protos(input_path: str, output_path: str, protoc: str = _default_protoc,
                    protol: str = _default_protol):
    try:
        os.mkdir(output_path)
    except FileExistsError:
        pass

    proto_sources = []

    for root, _, files in os.walk(input_path):
        for item in fnmatch.filter(files, "*.proto"):
            proto_sources.append(os.path.join(input_path, str(root), item))

    print("Generating Python classes from protobuf files...")
    subprocess.run([protoc,
           "--python_out=" + output_path,
           "--mypy_out=" + output_path,
           "--proto_path=" + input_path,
           "--experimental_allow_proto3_optional",  # Needed for old protoc versions
           *proto_sources],
           check=True)

    print("Post-processing with protoletariat...")
    subprocess.run([protol,
           "--dont-create-package",
           "--in-place",
           "--exclude-google-imports",
           "--python-out", output_path,
           "protoc",
           "--experimental_allow_proto3_optional",  # Needed for old protoc versions
           "--proto-path", input_path,
           *proto_sources],
           check=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()

    parser.add_argument("--input", default="proto-src")
    parser.add_argument("--output", default="klipy/proto")
    parser.add_argument("--protoc", help="Path to protoc", default=_default_protoc)
    parser.add_argument("--protol", help="Path to protoletariat", default=_default_protol)

    args = parser.parse_args()

    output_path = os.path.abspath(args.output)
    input_path = os.path.abspath(args.input)

    generate_protos(input_path, output_path, args.protoc, args.protol)
