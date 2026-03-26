# Copyright The KiCad Developers
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import json
from importlib.resources import files
from pathlib import Path
import re
from typing import Any
from jsonschema import Draft7Validator

from kipy.packaging.types import ValidationReport


def validate_plugin(source_dir: str | Path) -> ValidationReport:
    root = Path(source_dir).expanduser().resolve()
    report = ValidationReport(root=root)

    if not root.exists():
        report.add_error("plugin directory does not exist")
        return report

    if not root.is_dir():
        report.add_error("plugin path is not a directory")
        return report

    plugin_path = root / "plugin.json"

    if not plugin_path.exists():
        report.add_error("plugin.json does not exist")
        return report

    plugin = _load_plugin_json(plugin_path, report)
    _validate_requirements(root, report)

    if plugin is None:
        return report

    _validate_plugin_schema(plugin, report)
    _validate_plugin_data(root, plugin, report)
    return report


def _load_plugin_json(
    plugin_path: Path,
    report: ValidationReport,
) -> dict[str, Any] | None:
    if not plugin_path.exists():
        report.add_error("missing required file: plugin.json", path="plugin.json")
        return None

    try:
        data = json.loads(plugin_path.read_text(encoding="utf-8"))
    except OSError as ex:
        report.add_error(f"failed to read plugin.json: {str(ex)}", path="plugin.json")
        return None
    except json.JSONDecodeError as ex:
        report.add_error(f"invalid JSON: {str(ex)}", path="plugin.json")
        return None

    if not isinstance(data, dict):
        report.add_error("plugin.json root must be a JSON object", path="plugin.json")
        return None

    return data


def _validate_requirements(root: Path, report: ValidationReport):
    requirements_path = root / "requirements.txt"

    if not requirements_path.exists():
        report.add_warning(
            "missing requirements.txt, KiCad will not install any Python packages for your plugin",
            path="requirements.txt",
        )
        return

    try:
        lines = requirements_path.read_text(encoding="utf-8").splitlines()
    except OSError as ex:
        report.add_error(
            f"failed to read requirements.txt: {str(ex)}",
            path="requirements.txt",
        )
        return

    dependencies = [_parse_requirement_name(line) for line in lines]
    dependency_names = {name for name in dependencies if name is not None}

    if "kicad-python" not in dependency_names:
        report.add_warning(
            "requirements.txt does not include kicad-python",
            path="requirements.txt",
        )


def _validate_plugin_schema(plugin: dict[str, Any], report: ValidationReport):
    try:
        schema_text = (
            files("kipy.packaging.schemas")
            .joinpath("api.v1.schema.json")
            .read_text(encoding="utf-8")
        )
        schema = json.loads(schema_text)
    except Exception as e:
        report.add_error(
            f"failed to read plugin schema file: {str(e)}",
            path="kipy.packaging.schemas:api.v1.schema.json",
        )
        return

    validator = Draft7Validator(schema)

    for error in sorted(validator.iter_errors(plugin), key=lambda current: list(current.path)):
        report.add_error(
            f"schema validation error: {error.message}",
            path=_format_schema_error_path(error.path),
        )


def _validate_plugin_data(root: Path, plugin: dict[str, Any], report: ValidationReport):
    actions = plugin.get("actions")
    if not isinstance(actions, list):
        # Type checker assertion; should be enforced by schema
        return

    if len(actions) == 0:
        report.add_error(
            "plugin must define at least one action",
            path="plugin.json:actions",
        )
        return

    for action_index, action in enumerate(actions):
        if not isinstance(action, dict):
            # Type checker assertion; should be enforced by schema
            continue

        prefix = f"plugin.json:actions[{action_index}]"
        entrypoint = action.get("entrypoint")

        if isinstance(entrypoint, str):
            _validate_file_path(
                root,
                entrypoint,
                report,
                f"{prefix}:entrypoint",
                "entrypoint file",
            )

        for key in ("icons-light", "icons-dark"):
            icons = action.get(key)
            if not isinstance(icons, list):
                # Not an error; optional
                continue

            for icon_index, icon in enumerate(icons):
                if not isinstance(icon, str) or icon.strip() == "":
                    continue

                _validate_file_path(
                    root,
                    icon,
                    report,
                    f"{prefix}:{key}[{icon_index}]",
                    "icon file",
                )


def _validate_file_path(
    root: Path,
    value: str,
    report: ValidationReport,
    path: str,
    description: str,
):
    candidate = Path(value)

    if candidate.is_absolute():
        report.add_error(f"{description} path must be relative", path=path)
        return

    resolved = (root / candidate).resolve()
    if not resolved.exists():
        report.add_error(f"{candidate.name} does not exist", path=path)
        return

    if not resolved.is_file():
        report.add_error(f"{candidate.name} is not a file", path=path)
        return

    try:
        resolved.relative_to(root)
    except ValueError:
        report.add_error(f"{resolved.name} resolves outside plugin directory", path=path)


def _format_schema_error_path(path: Any) -> str:
    parts = list(path)

    if len(parts) == 0:
        return "plugin.json"

    output = "plugin.json"
    for part in parts:
        if isinstance(part, int):
            output += f"[{part}]"
        else:
            output += f":{part}"

    return output


_REQUIREMENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*")

def _parse_requirement_name(line: str) -> str | None:
    text = line.strip()

    if text == "" or text.startswith("#"):
        return None

    match = _REQUIREMENT_NAME_PATTERN.match(text)

    if match is None:
        return None

    return match.group(0)
