"""Step schemas are Orbit component trees. The document stores JSON; we hydrate real components."""

from __future__ import annotations

from typing import Any

from almasix.orbit.forms import (
    Builder,
    Checkbox,
    CheckboxList,
    CodeEditor,
    ColorPicker,
    DatePicker,
    DateTimePicker,
    FileUpload,
    Hidden,
    KeyValue,
    MarkdownEditor,
    ModalTableSelect,
    MoneyInput,
    MonthPicker,
    MorphToSelect,
    MultiSelect,
    OneTimeCodeInput,
    Placeholder,
    Radio,
    RelationshipRepeater,
    Repeater,
    RichEditor,
    Select,
    Slider,
    TableSelect,
    TagsInput,
    Textarea,
    TextInput,
    TimePicker,
    Toggle,
    ToggleButtons,
    ViewField,
    WeekPicker,
    YearPicker,
)
from almasix.orbit.schemas import (
    Callout,
    EmptyState,
    Fieldset,
    Flex,
    Grid,
    Group,
    Icon,
    Image,
    Section,
    Split,
    Tabs,
    Text,
    UnorderedList,
    Wizard,
)
from almasix.orbit.support.html import e
from almasix_orbit_workflows.errors import Invalid
from almasix_orbit_workflows.predicates import matches

FIELDS = {
    "TextInput": TextInput,
    "Textarea": Textarea,
    "Select": Select,
    "Checkbox": Checkbox,
    "Toggle": Toggle,
    "DatePicker": DatePicker,
    "DateTimePicker": DateTimePicker,
    "TimePicker": TimePicker,
    "WeekPicker": WeekPicker,
    "MonthPicker": MonthPicker,
    "YearPicker": YearPicker,
    "FileUpload": FileUpload,
    "Hidden": Hidden,
    "Placeholder": Placeholder,
    "Radio": Radio,
    "CheckboxList": CheckboxList,
    "TagsInput": TagsInput,
    "ColorPicker": ColorPicker,
    "MoneyInput": MoneyInput,
    "RichEditor": RichEditor,
    "MarkdownEditor": MarkdownEditor,
    "KeyValue": KeyValue,
    "Repeater": Repeater,
    "Builder": Builder,
    "Slider": Slider,
    "ToggleButtons": ToggleButtons,
    "CodeEditor": CodeEditor,
    "MultiSelect": MultiSelect,
    "OneTimeCodeInput": OneTimeCodeInput,
    "ViewField": ViewField,
    "MorphToSelect": MorphToSelect,
    "TableSelect": TableSelect,
    "ModalTableSelect": ModalTableSelect,
    "RelationshipRepeater": RelationshipRepeater,
}

LAYOUTS = {
    "Grid": Grid,
    "Flex": Flex,
    "Group": Group,
    "Split": Split,
    "Section": Section,
    "Fieldset": Fieldset,
    "Callout": Callout,
    "EmptyState": EmptyState,
    "Text": Text,
    "Icon": Icon,
    "Image": Image,
    "UnorderedList": UnorderedList,
}


class SignatureInput:
    """Drawn or typed signature. Rendered with the other fields, stored on the submission."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._label = "Signature"
        self._required = True

    @classmethod
    def make(cls, name: str) -> SignatureInput:
        return cls(name)

    def label(self, text: str) -> SignatureInput:
        self._label = text
        return self

    def required(self, condition: bool = True) -> SignatureInput:
        self._required = condition
        return self

    def get_state_path(self) -> str:
        return self._name

    def render(self, state: Any = None, **ctx: Any) -> str:
        del ctx
        value = "" if state is None else e(state)
        return (
            f'<label class="or-field or-field-Signature">{e(self._label)}'
            f'<textarea class="or-input" name="{e(self._name)}" data-signature="1" '
            f'required>{value}</textarea></label>'
        )


FIELDS["SignatureInput"] = SignatureInput


def hydrate(node: dict[str, Any]) -> Any:
    """Build one Orbit component. Nested ``schema``, ``tabs``, and ``steps`` are walked."""
    kind = str(node.get("type") or "")
    name = node.get("name")
    if kind in LAYOUTS:
        component = LAYOUTS[kind].make(name)
        _apply_common(component, node)
        children = [hydrate(child) for child in node.get("schema") or []]
        if children and hasattr(component, "schema"):
            component.schema(children)
        return component
    if kind == "Tabs":
        component = Tabs.make(name)
        tabs = []
        for tab in node.get("tabs") or []:
            tabs.append(
                {
                    "label": tab.get("label") or "Tab",
                    "schema": [hydrate(child) for child in tab.get("schema") or []],
                    "icon": tab.get("icon"),
                }
            )
        return component.tabs(*tabs)
    if kind == "Wizard":
        component = Wizard.make(name)
        pages = []
        for page in node.get("steps") or []:
            pages.append(
                {
                    "label": page.get("label") or "Page",
                    "schema": [hydrate(child) for child in page.get("schema") or []],
                }
            )
        return component.steps(*pages)
    field_cls = FIELDS.get(kind)
    if field_cls is None:
        raise Invalid(f"Unknown schema component '{kind}'.")
    field = field_cls.make(str(name or kind))
    _apply_common(field, node)
    nested = [hydrate(child) for child in node.get("schema") or []]
    if nested and hasattr(field, "schema"):
        field.schema(nested)
    return field


def render_schema(nodes: list[dict[str, Any]], answers: dict[str, Any], **ctx: Any) -> str:
    """HTML for a step. Hidden-by-rule nodes are omitted, so a later step can depend on earlier answers."""
    parts: list[str] = []
    for node in nodes:
        if not _shown(node, answers):
            continue
        component = hydrate(node)
        parts.append(str(component.render(answers, **ctx)))
    return "".join(parts)


def field_errors(nodes: list[dict[str, Any]], payload: dict[str, Any], answers: dict[str, Any]) -> dict[str, str]:
    """Required fields that are visible and empty. Keys are field names."""
    errors: dict[str, str] = {}
    for node in _walk(nodes):
        if not _shown(node, {**answers, **payload}):
            continue
        if node.get("type") not in FIELDS and node.get("type") != "SignatureInput":
            continue
        if not node.get("required"):
            continue
        name = str(node.get("name") or "")
        value = payload.get(name, answers.get(name))
        if value is None or value == "" or value == []:
            errors[name] = f"{node.get('label') or name} is required."
    return errors


def _apply_common(component: Any, node: dict[str, Any]) -> None:
    if node.get("label") and hasattr(component, "label"):
        component.label(node["label"])
    if node.get("heading") and hasattr(component, "heading"):
        component.heading(node["heading"])
    if node.get("required") and hasattr(component, "required"):
        component.required(True)
    if node.get("placeholder") and hasattr(component, "placeholder"):
        component.placeholder(node["placeholder"])
    if "columns" in node and hasattr(component, "columns"):
        component.columns(int(node["columns"]))
    if node.get("options") and hasattr(component, "options"):
        component.options(node["options"])
    if node.get("content") and hasattr(component, "content"):
        component.content(node["content"])


def _shown(node: dict[str, Any], answers: dict[str, Any]) -> bool:
    rule = node.get("visible_when")
    if not rule:
        return True
    return matches(str(rule), answers, {}, {})


def _walk(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for node in nodes:
        found.append(node)
        for key in ("schema",):
            found.extend(_walk(node.get(key) or []))
        for tab in node.get("tabs") or []:
            found.extend(_walk(tab.get("schema") or []))
        for page in node.get("steps") or []:
            found.extend(_walk(page.get("schema") or []))
    return found
