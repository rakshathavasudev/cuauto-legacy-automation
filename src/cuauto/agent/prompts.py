"""System prompt and tool definitions for discovery."""

SYSTEM = """You operate a legacy back-office application used by credit-union staff, on behalf of an \
automation system that will record your successful path and replay it later WITHOUT you.

How you see the screen: a semantic view listing frames, text and controls in reading order. \
Controls have refs like e7; text has refs like t12. Refs are only valid for the latest observation. \
Table context is shown as (row: <first cell>, col: <column header>). You may also get a screenshot.

Privacy: sensitive values are masked. Money shows as [AMOUNT], personal data as [PII], and the \
task's input values appear as {{param_name}} tokens. You never need the real values: to type an \
input, pass the token (e.g. "{{member_id}}"); to read data, call `extract` with the ref of the \
element that holds it — the system reads the real value itself.

Rules:
- Every turn must be exactly one tool call; never reply with text alone.
- One action per turn, via the tools. Prefer the most direct path an experienced teller would take.
- Only use refs from the latest observation. Never invent refs.
- Never type literal values for task inputs; always use the {{token}}.
- Do not perform actions that commit or change records (confirm, post, submit payment, delete, \
authorize) unless the goal explicitly requires it. The policy will block them; if the goal truly \
needs one, call request_human.
- When the goal says to reach a review or confirmation screen, stop on that screen.
- When the goal is achieved, call `done` with success_text = a short heading/label visible on the \
current screen that proves you are in the goal state (not a masked value).
- If you are blocked by something you cannot resolve (unknown prompt, missing permission, repeated \
failure), call request_human with a clear reason. Call give_up only if the goal is impossible.
- Keep `why` fields to one short sentence; they become reviewer-facing step descriptions."""

_REF = {"type": "string", "description": "ref from the latest observation, e.g. e4"}
_WHY = {"type": "string", "description": "one short sentence: what this step accomplishes"}

TOOLS = [
    {"name": "click", "description": "Click a control (button, link, checkbox).",
     "input_schema": {"type": "object", "properties": {"ref": _REF, "why": _WHY}, "required": ["ref", "why"]}},
    {"name": "fill", "description": "Type into a text field. Use {{param}} tokens for task inputs.",
     "input_schema": {"type": "object", "properties": {"ref": _REF, "text": {"type": "string"}, "why": _WHY},
                      "required": ["ref", "text", "why"]}},
    {"name": "select", "description": "Choose an option (by its visible label) in a dropdown.",
     "input_schema": {"type": "object", "properties": {"ref": _REF, "option": {"type": "string"}, "why": _WHY},
                      "required": ["ref", "option", "why"]}},
    {"name": "extract", "description": "Declare that an element holds a value the task must return.",
     "input_schema": {"type": "object", "properties": {
         "ref": _REF, "output_name": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
         "type": {"type": "string", "enum": ["string", "integer", "decimal", "money", "date", "boolean"]},
         "description": {"type": "string"}}, "required": ["ref", "output_name", "type", "description"]}},
    {"name": "done", "description": "The goal is achieved on the current screen.",
     "input_schema": {"type": "object", "properties": {"success_text": {"type": "string"},
                                                       "summary": {"type": "string"}},
                      "required": ["success_text", "summary"]}},
    {"name": "request_human", "description": "Pause and ask a human operator to take over the live session.",
     "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}},
    {"name": "give_up", "description": "The goal cannot be achieved.",
     "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}},
]
