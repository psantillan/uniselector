"""The plan: what the main agent hands off. It is a list of intents, not a list of operations."""
from jsonschema import Draft202012Validator

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intents", "handles"],
    "properties": {
        "intents": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1},
                    "description": "The steps, in order. Each one is a sentence, for example: filter the events to purchases."},
        "handles": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1},
                    "description": "The handle ids to start from."},
        "notes": {"type": "string", "description": "Optional. Facts that the plan needs and the intents do not state."},
        "limits": {"type": "object", "additionalProperties": False,
                   "description": "Optional. Lower limits than the configured ones.",
                   "properties": {"budget_tokens": {"type": "integer", "minimum": 1}, "wall_seconds": {"type": "number", "exclusiveMinimum": 0}}},
    },
}


def violations(schema: dict, value) -> list[str]:
    return [f"{'/'.join(map(str, e.path)) or '.'}: {e.message}" for e in Draft202012Validator(schema).iter_errors(value)]


def check(plan) -> dict:
    bad = violations(PLAN_SCHEMA, plan)
    if bad:
        raise ValueError(f"the plan is not valid: {bad[0]}")
    return plan


def limits(cfg, plan: dict) -> tuple[int, float]:
    """The limits of one run: the configured limits, or the lower limits of the plan."""
    asked = plan.get("limits", {})
    return (min(cfg.budget_tokens, asked.get("budget_tokens", cfg.budget_tokens)),
            min(cfg.wall_seconds, asked.get("wall_seconds", cfg.wall_seconds)))
