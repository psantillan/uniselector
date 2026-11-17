"""The plan: what the main agent hands off before it sleeps.

A plan is the objects that the main agent built, and the essential variables. An object is a handle
and one operation that reads a bounded value from it. An essential variable is a sentence that is
true while the main agent can sleep. An object can carry variables of its own, and the names of its
runners: a runner is what answers for the object when a variable is not true. Until then a runner
does nothing. An object can carry forms of its output also: each form has the runners that make it
before a wake.
"""
from jsonschema import Draft202012Validator

OBJECT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["name", "handle", "operation"],
    "properties": {
        "name": {"type": "string", "minLength": 1,
                 "description": "The name of the object. It stays the same when you replace the handle or the operation behind it."},
        "handle": {"type": "string", "minLength": 1, "description": "The id of the handle that you built with the DataHandles tools."},
        "operation": {"type": "object", "additionalProperties": False, "required": ["capability"],
                      "description": "One operation on the handle that gives a small value. The run does it again after each change of the handle.",
                      "properties": {"capability": {"type": "string", "minLength": 1}, "input": {"type": "object"}}},
        "variables": {"type": "array", "items": {"type": "string", "minLength": 1},
                      "description": "Optional. The check of this object: sentences that are true while it works. When one is not true, "
                                     "the wake goes to the runner of this object. A check rejects only what it states. For an object "
                                     "with runners, state the meaning of each condition that a replacement must keep, with no column "
                                     "name that can change, for example: It sums only the paid orders. The decision model reads how "
                                     "the handle is made, so it can judge such a sentence. A check can "
                                     "state also what the runners of the object can spend, for example: The runners of this object spent "
                                     "less than 20000 tokens."},
        "runners": {"type": "array", "items": {"type": "string", "minLength": 1},
                    "description": "Optional. Who answers for this object, in order. A runner does nothing while each variable is true. "
                                   "At a wake, the first runner gives one replacement of the object, or confirms its action. A runner "
                                   "that gives nothing, or whose replacement does not pass the check, leaves the list, and the next one "
                                   "has its place. After the last one, the wake comes to you. `main` is you. `human` is the user, for an action only. Each other name is a model "
                                   "runner of the configuration, or a callable of your code: `FILE.py:NAME` with an absolute path. NAME is "
                                   "a function `NAME(client)` that returns the runner: a function `runner(question, submit, spend, "
                                   "seconds)`. `client` is a DataHandles SDK client. `question` has the object, the graph that made its handle, its sources and what "
                                   "is not true now. The runner builds a new handle and calls `submit({\"handle\": ID, \"operation\": "
                                   "{...}})`: the result is None, or the fault of the value. For a form, it calls `submit(VALUE)`."},
    },
}
_RUNNERS = {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}
OBJECT_SCHEMA["properties"]["forms"] = {
    "type": "object",
    "description": "Optional. Forms of the output of this object that a runner makes, for example a summary or an embedding. Each key "
                   "is the name of a form. With no key, the object has no such form.",
    "additionalProperties": {"type": "object", "additionalProperties": False, "required": ["runners"], "properties": {
        "runners": {**_RUNNERS, "description": "Who makes the form, in order: model runners and callables. No runner works while each "
                                               "variable is true. Before a wake, the first one makes the form one time, and the wake has "
                                               "it. A runner that gives no form, or whose form does not pass the check, leaves the list."},
        "variables": {"type": "array", "items": {"type": "string", "minLength": 1},
                      "description": "Optional. The check of the form: sentences that are true of a good form. The runner reads them."}}},
}
_VARIABLES = {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1},
              "description": "The essential variables. Each one is a sentence that is true while you can sleep, and that the outputs of the objects "
                             "show to be true or false. State a goal as the sentence that is true before the goal is met, for example: "
                             "The count is less than 200."}
PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["objects", "variables"],
    "properties": {
        "objects": {"type": "array", "minItems": 1, "items": OBJECT_SCHEMA},
        "variables": _VARIABLES,
        "notes": {"type": "string", "description": "Optional. Facts that a reader of the outputs needs and the variables do not state."},
        "limits": {"type": "object", "additionalProperties": False,
                   "description": "Optional. Lower limits than the configured ones.",
                   "properties": {"budget_tokens": {"type": "integer", "minimum": 1}, "wall_seconds": {"type": "number", "exclusiveMinimum": 0}}},
    },
}
# What the main agent writes back at a wake. Only what it names changes.
ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False, "minProperties": 1,
    "properties": {
        "objects": {"type": "array", "items": OBJECT_SCHEMA,
                    "description": "The new versions. An object with a name of the run replaces that object, and it keeps the variables, the "
                                   "runners and the forms of that object when it does not state them. Each other object stays as it is."},
        "remove": {"type": "array", "items": {"type": "string"}, "description": "The names of the objects that the run must drop."},
        "variables": {**_VARIABLES, "description": "A new list of essential variables, when the old list is not correct now."},
        "end": {"const": True, "description": "End the run after the objects of this answer ran one time."},
    },
}


def violations(schema: dict, value) -> list[str]:
    return [f"{'/'.join(map(str, e.path)) or '.'}: {e.message}" for e in Draft202012Validator(schema).iter_errors(value)]


def check(value, schema: dict = PLAN_SCHEMA, what: str = "plan") -> dict:
    bad = violations(schema, value)
    if bad:
        raise ValueError(f"the {what} is not valid: {bad[0]}")
    names = [o["name"] for o in value.get("objects", [])]
    if len(set(names)) < len(names):
        raise ValueError(f"the {what} is not valid: two objects have one name")
    return value


def limits(cfg, plan: dict) -> tuple[int, float]:
    """The limits of one run: the configured limits, or the lower limits of the plan."""
    asked = plan.get("limits", {})
    return (min(cfg.budget_tokens, asked.get("budget_tokens", cfg.budget_tokens)),
            min(cfg.wall_seconds, asked.get("wall_seconds", cfg.wall_seconds)))
