"""Safe YAML and SDK-backed template validation."""

import hashlib
import json
from collections.abc import Hashable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import cast

import yaml
from pydantic import ValidationError
from yaml.nodes import MappingNode

from jevlab.core.diagnostics import redact_text
from jevlab.core.errors import JevError
from jevlab.core.files import atomic_write, read_text
from jevlab.core.models import Template

# A local workbench bound, independent of a provider's context limit. This stays
# below the nesting that Python/YAML/Pydantic serializers can process reliably.
MAX_NESTING = 64


def nesting_error(format: str) -> ValueError:
    return ValueError(
        f"{format} is too deeply nested. Reduce nested lists or mappings to at most "
        f"{MAX_NESTING} levels before continuing."
    )


class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Hashable, object]:
        seen: set[object] = set()
        for key, _ in node.value:
            value = self.construct_object(key, deep=deep)
            if not isinstance(value, str):
                raise ValueError('YAML mapping keys must be strings; quote "true" and "false".')
            if value in seen:
                raise ValueError("Duplicate YAML mapping key.")
            seen.add(value)
        return super().construct_mapping(node, deep=deep)


def load_yaml(text: str) -> object:
    if len(text.encode()) > 2_000_000:
        raise ValueError("Template exceeds the 2 MB limit.")
    # Aliases are unnecessary here and can produce cyclic or exponentially expanded input.
    try:
        depth = 0
        for token in yaml.scan(text):
            if isinstance(token, (yaml.AliasToken, yaml.AnchorToken)):
                raise ValueError("YAML anchors and aliases are not supported.")
            if isinstance(
                token,
                (
                    yaml.BlockMappingStartToken,
                    yaml.BlockSequenceStartToken,
                    yaml.FlowMappingStartToken,
                    yaml.FlowSequenceStartToken,
                ),
            ):
                depth += 1
                if depth > MAX_NESTING:
                    raise nesting_error("YAML")
            elif isinstance(
                token, (yaml.BlockEndToken, yaml.FlowMappingEndToken, yaml.FlowSequenceEndToken)
            ):
                depth -= 1
        return yaml.load(text, Loader=UniqueLoader)
    except RecursionError:
        raise nesting_error("YAML") from None


def load_json(text: str) -> object:
    """Parse editable JSON without turning unsupported nesting into a UI crash."""
    if len(text.encode()) > 2_000_000:
        raise ValueError("JSON exceeds the 2 MB limit. Shorten it before continuing.")
    try:
        value = json.loads(text)
    except RecursionError:
        raise nesting_error("JSON") from None
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, (dict, list)):
            if depth >= MAX_NESTING:
                raise nesting_error("JSON")
            pending.extend(
                (child, depth + 1) for child in (item.values() if isinstance(item, dict) else item)
            )
    return value


def validation_message(error: ValueError | yaml.YAMLError) -> str:
    """Keep field locations and parser reasons without echoing the submitted input."""
    if isinstance(error, ValidationError):
        return redact_text(
            "; ".join(
                f"{'.'.join(map(str, item['loc'])) or 'template'}: {item['msg']}"
                for item in error.errors(include_input=False, include_url=False)[:5]
            )
        )
    if isinstance(error, json.JSONDecodeError):
        return f"Invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}."
    if isinstance(error, yaml.MarkedYAMLError):
        mark = error.problem_mark
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        return redact_text(f"Invalid YAML{location}: {error.problem or 'check the structure'}.")
    return redact_text(str(error))


def parse_template(text: str, *, historical: bool = False) -> Template:
    try:
        return Template.model_validate(load_yaml(text), context={"historical": historical})
    except ValidationError as error:
        raise JevError(
            "invalid_template", validation_message(error), "Correct the named field and save again."
        ) from None
    except (ValueError, yaml.YAMLError) as error:
        raise JevError(
            "invalid_yaml",
            validation_message(error),
            'Use unique string keys, quote "true"/"false", and remove anchors or custom tags.',
        ) from None
    except (TypeError, RecursionError):
        raise JevError(
            "invalid_yaml",
            "The template structure is too deeply nested or unsupported.",
            "Use a mapping of named fields and remove recursive structures.",
        ) from None


def dump_template(template: Template) -> str:
    return yaml.safe_dump(template.model_dump(mode="json"), sort_keys=False, allow_unicode=True)


def revision_hash(template: Template) -> str:
    canonical = json.dumps(template.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class TemplateSource:
    """A validated design and its save destination, separate from its API payload."""

    template: Template
    path: Path
    project_file: bool
    content_hash: str | None = None


def is_template_path(reference: str | Path) -> bool:
    """A bare slug always selects the catalog, regardless of files in the cwd."""
    return (
        isinstance(reference, Path)
        or "/" in reference
        or reference.lower().endswith((".yaml", ".yml"))
    )


def _read_template_file(path: Path) -> str:
    try:
        return read_text(path)
    except FileNotFoundError:
        raise JevError(
            "not_found",
            "The template YAML file does not exist.",
            "Check the path relative to your current directory, or use an absolute path.",
        ) from None
    except IsADirectoryError:
        raise JevError(
            "file_error", "The template path is a directory.", "Choose a .yaml or .yml file."
        ) from None
    except (OSError, UnicodeError, ValueError):
        raise JevError(
            "file_error",
            "The template YAML file could not be read.",
            "Use a UTF-8 file below 2 MB and check its read permissions.",
        ) from None


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Templates:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, name: str) -> Path:
        import re

        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
            raise JevError("invalid_name", "Invalid template name.", "Use a lowercase slug.")
        return self.root / f"{name}.yaml"

    def load(self, name: str) -> Template:
        path = self.path(name)
        try:
            template = parse_template(read_text(path))
        except FileNotFoundError:
            raise JevError(
                "not_found", "Template not found.", "Run jevlab templates to list names."
            ) from None
        except (OSError, UnicodeError, ValueError):
            raise JevError(
                "file_error", "Cannot read template.", "Check its size and permissions."
            ) from None
        if template.name != name:
            raise JevError(
                "invalid_name", "Template name differs from its filename.", "Make them match."
            )
        return template

    def resolve(self, reference: str | Path) -> TemplateSource:
        """Resolve a catalog name or an explicit project YAML path, without importing it.

        Relative files resolve against the caller's current working directory. The
        YAML's internal name remains the run identity; a project filename need not
        match it. ``load`` remains catalog-only for server and internal name lookups.
        """
        if not is_template_path(reference):
            name = str(reference)
            return TemplateSource(self.load(name), self.path(name), False)
        path = Path(reference).expanduser()
        if path.suffix.lower() not in (".yaml", ".yml"):
            raise JevError(
                "invalid_template_path",
                "A project template needs a .yaml or .yml filename.",
                "Use a YAML path such as ./decisions/routing.yaml, or a saved template name.",
            )
        try:
            path = path.resolve()
        except (OSError, RuntimeError):
            raise JevError(
                "file_error",
                "The template path cannot be resolved.",
                "Check its folders and links.",
            ) from None
        text = _read_template_file(path)
        return TemplateSource(parse_template(text), path, True, _content_hash(text))

    def load_reference(self, reference: str | Path) -> Template:
        """Load a named or project-owned design for a CLI workflow."""
        return self.resolve(reference).template

    def save_source(self, source: TemplateSource, template: Template) -> TemplateSource:
        """Save to the opened file, rejecting external changes instead of overwriting them."""
        template = parse_template(dump_template(template))
        if not source.project_file:
            self.save(template, overwrite=template.name == source.template.name)
            return self.resolve(template.name)
        try:
            current = _read_template_file(source.path)
        except JevError as error:
            if error.code != "not_found":
                raise
            raise JevError(
                "template_changed",
                "The project template was removed or moved while it was open.",
                "Keep your draft, restore the original file, or reopen its new location.",
            ) from None
        if _content_hash(current) != source.content_hash:
            raise JevError(
                "template_changed",
                "The project template changed outside this editor.",
                "Keep your draft and compare it with the file before reopening. "
                "The external changes were not overwritten.",
            )
        text = dump_template(template)
        atomic_write(source.path, text)
        return TemplateSource(template, source.path, True, _content_hash(text))

    def save(self, template: Template, *, overwrite: bool = False) -> Path:
        # Revalidate mutable nested dictionaries before writing.
        template = parse_template(dump_template(template))
        path = self.path(template.name)
        if path.exists() and not overwrite:
            raise JevError(
                "already_exists", "That template already exists.", "Edit it or choose a new name."
            )
        atomic_write(path, dump_template(template))
        return path

    def names(self) -> list[str]:
        return sorted(path.stem for path in self.root.glob("*.yaml"))

    def seed(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.path("support-triage").exists():
            source = files("jevlab.resources").joinpath("support-triage.yaml").read_text()
            self.save(parse_template(source))


def fork_template(template: Template, name: str) -> Template:
    data = template.model_dump(mode="json")
    data["name"] = name
    return Template.model_validate(data)


def context_estimate(template: Template, state: object) -> dict[str, object]:
    """Rough UTF-8 character estimate, never an assertion of server token counts."""
    state_size = len(json.dumps(state, ensure_ascii=False))
    questions = [len(q.model_dump_json()) for q in template.questions.values()]
    combined = (state_size + sum(questions) + 3) // 4
    longest = (state_size + max(questions) + 3) // 4
    warnings = []
    if combined >= 51_200 or longest >= 25_600:
        warnings.append("Estimated context is near/over a limit; trim state or questions.")
    return cast(
        dict[str, object],
        {
            "estimated_total_tokens": combined,
            "estimated_state_plus_longest": longest,
            "total_limit": 64_000,
            "state_plus_longest_limit": 32_000,
            "method": "characters/4; approximate, especially for non-English text",
            "warnings": warnings,
        },
    )
