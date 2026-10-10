"""The engine built into the API: what answers when no separate resume-engine service is set up.

`GenericBackend` (`backend.py`) is the only thing the rest of the code imports. Everything else
in this package is the engine itself, in small modules that each own one decision:

- `sources.py` the profile as pointers, and the text each pointer may be written from
- `job.py`, `step0.py` a job posting cleaned for a prompt, and the read of what it asks for
- `provenance.py` the fabrication check
- `plan.py` how much fits, which entries are kept and which bullets the model is shown
- `prompts.py`, `llm.py` the words the model is asked for, and the calls that ask
- `body.py`, `summary.py`, `cover.py` the stages that turn a model's answer into checked content
- `assemble.py`, `document.py` the resume's content, filled to a page budget
- `latex.py`, `templates.py`, `formatting.py` the escaping, the two documents, and dates
- `header.py`, `plaintext.py` the deterministic operations (header chips, resume text)
- `pipeline.py` the order they run in, and the bounds on cost and time

The README's "How the built-in engine works" says what it guarantees and what it does not.
"""

from .backend import GENERIC_CAPABILITIES, GenericBackend, not_available

__all__ = ["GENERIC_CAPABILITIES", "GenericBackend", "not_available"]
