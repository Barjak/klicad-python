#!/usr/bin/env python3

# Copyright The KiCad Developers
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
import code

from kipy import KiCad


def main():
    parser = argparse.ArgumentParser(
        description="Connect to a headless KiCad API server started by kicad-python"
    )
    parser.add_argument(
        "file",
        nargs="?",
        default=None,
        help="Optional .kicad_pcb, .kicad_sch, or .kicad_pro path to preload",
    )
    parser.add_argument(
        "--kicad-cli",
        dest="kicad_cli_path",
        default=None,
        help="Optional explicit path to the kicad-cli binary",
    )
    args = parser.parse_args()

    with KiCad(
        headless=True,
        kicad_cli_path=args.kicad_cli_path,
        file_path=args.file,
    ) as kicad:
        version = kicad.get_version()
        print(f"Connected to headless KiCad {version.full_version}")

        banner = (
            "\nHeadless KiCad REPL\n"
            "The variable 'k' is a connected KiCad object.\n"
            "Try: k.get_version(), k.get_board(), or explore with dir(k).\n"
            "Press Ctrl-D (macOS/Linux) or Ctrl-Z then Enter (Windows) to exit.\n"
        )
        namespace = {"k": kicad}
        code.interact(banner=banner, local=namespace)


if __name__ == "__main__":
    main()
