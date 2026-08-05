"""The prompt registry: a prompt is a name, a version, its text and the shape it asks for.

Ask for one by `(name, version)`. That is the only lookup there is, and its absence of a
`latest` is the design.
"""

from app.prompts.library import ALL
from app.prompts.registry import DuplicatePrompt, Prompt, UnknownPrompt, index

_BY_KEY = index(ALL)


def get(name: str, version: str) -> Prompt:
    """The prompt registered under exactly this name and version, or `UnknownPrompt`.

    **There is no "latest", and adding one would be the bug.** This is the same rule the
    codebase applies to template lineage — resolve through `(family_id, version)`, never the
    newest row — and it is here for the same reason, one step upstream. Editing a prompt
    writes a new version; a caller that took the newest would silently start sending different
    words while every trace row, every evaluation baseline and every draft already written
    still named the old one. That defect has been written three times in this repo against
    templates (`regenerate_visual`, `regenerate_text`, `retopic`) and each time it was silent:
    the output looked fine and the attribution was wrong.

    So a miss raises rather than degrading to a near match. A caller naming a version that was
    never registered has a typo or a bad deploy, and failing at the lookup is cheaper than
    discovering it later from a trace row that cannot be reproduced.

    Callers pin the version at module level (`_WRITE = prompts.get("draft.write", "1.0.0")`)
    rather than at each call, so the version a file sends is one greppable line.
    """
    try:
        return _BY_KEY[(name, version)]
    except KeyError:
        raise UnknownPrompt(f"no prompt {name!r} at version {version!r}") from None


__all__ = ["ALL", "DuplicatePrompt", "Prompt", "UnknownPrompt", "get", "index"]
