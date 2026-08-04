"""What a prompt *is* here, and the one rule for finding one.

Machinery only — the prompts themselves live in `library.py`, so that adding a version is
an edit to content and never to lookup.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# MAJOR.MINOR.PATCH, checked at construction. Not a semver library and not a comparison:
# nothing here orders versions (see `app.prompts.get`), so all this buys is that `"1.0"` and
# `"1.0.0"` cannot both exist and quietly name the same intent. A caller asking for `"1.0"`
# would then miss a prompt registered as `"1.0.0"` and get an `UnknownPrompt` at the moment
# a draft was being written.
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class UnknownPrompt(LookupError):
    """Nothing is registered under that exact (name, version).

    Raised rather than answered with the nearest match, which is the whole point — see the
    comment on `app.prompts.get`.
    """


class DuplicatePrompt(ValueError):
    """Two prompts claim the same (name, version).

    Caught when the library is indexed, at import, because the alternative is one of them
    silently winning and every trace row afterwards naming a prompt that did not run.
    """


@dataclass(frozen=True)
class Prompt:
    """One addressable prompt: what it is called, which version, its text, its output shape.

    **`text` is the complete system message, including its trailing `Return ONLY JSON …`
    block.** `output_schema` is a declaration *alongside* that block, never a replacement for
    it: lifting the shape out of the text to avoid stating it twice would change the bytes
    sent to the model, and the whole reason these constants moved here was that nothing about
    the model's input may change in the move.

    ponytail: no owner field and no changelog file. With one operator an `owner` column holds
    the same name on every row — the reason `Publication` has no `actor` — and git already
    answers "what changed and when" for a text file. Both arrive with a second person.
    """

    name: str
    version: str
    text: str

    # The JSON shape the model is told to return, as a JSON-Schema-shaped dict.
    #
    # **Declared, not enforced.** Nothing validates against it yet; hard gates are a later
    # slice, and a validator switched on here would start failing drafts that the callers
    # deliberately tolerate — `_by_name` falls back to the first approved template precisely
    # so a model naming something that does not exist does not cost the operator a draft.
    # `required` therefore states what the prompt *demands of the model*, which is a stricter
    # thing than what today's caller can survive. Reconcile the two when the gate lands, and
    # reconcile it towards the prompt.
    output_schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not _SEMVER.match(self.version):
            raise ValueError(
                f"prompt {self.name!r} version {self.version!r} is not MAJOR.MINOR.PATCH"
            )


def index(prompts: tuple[Prompt, ...]) -> dict[tuple[str, str], Prompt]:
    """The library as a lookup table, refusing a collision instead of picking a winner."""
    table: dict[tuple[str, str], Prompt] = {}
    for prompt in prompts:
        key = (prompt.name, prompt.version)
        if key in table:
            raise DuplicatePrompt(f"{prompt.name} {prompt.version} is registered twice")
        table[key] = prompt
    return table
