"""The walk: one tree of single-pass decisions that chooses an operation and fills its input.

The root decision chooses among the capabilities of the current handles and the control options.
The walk then follows the JSON input schema of the chosen capability. Each schema node is one
decision: an enum, a `$handle` input, an optional property, one more array item, or an open value
from the candidate pool. A decision that abstains, or that chooses `none fits`, becomes a question.
"""
import json
import time
from dataclasses import dataclass, field

from .decide import LABELS, judge
from .pool import NONE_FITS
from .state import cut

CONTROLS = {
    "step_done": "the results show that the current intent is met. Go to the next intent.",
    "wait": "a job, a stream or a response is not ready. Wait a short time, then look again.",
    "wake_agent": "return to the main agent. The plan cannot continue as it is: the main agent must make a new plan.",
    "abort": "stop the run now. To continue can do harm.",
}
REASONS = [
    "the handles cannot carry out the current intent",
    "an error does not go away",
    "the run stalled: nothing changes",
    "the limits are not enough for the rest of the plan",
    "the current intent is not clear",
]
_MORE = object()


@dataclass
class Option:
    text: str       # what the decision model reads
    value: object   # what the walk gets when the model chooses the option
    key: object     # the JSON form of the option, for the logs and for a question


@dataclass
class Context:
    """The operation that the walk fills."""
    entry: object
    capability: dict
    holder: dict   # {"input": the input chosen so far}
    properties: dict = field(default_factory=dict)   # id of an object in the input -> the schemas of its properties


def path_text(path: list) -> str:
    out = ""
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else f".{part}" if out else part
    return out or "input"


def is_handle_input(schema: dict) -> bool:
    return schema.get("type") == "object" and schema.get("required") == ["$handle"]


def kinds(schema: dict) -> str:
    if "enum" in schema:
        return "one of " + json.dumps(schema["enum"])
    kind = schema.get("type")
    return " or ".join(f"a {k}" for k in (kind if isinstance(kind, list) else [kind])) if kind else cut(schema, 120)


def form(schema: dict, box, key) -> str:
    """One branch of an anyOf, shown as the input that it gives: the container, with a mark at the place of the value."""
    many = schema.get("type") == "array"
    mark = "[x, ...]" if many else "x"
    if isinstance(box, dict) and key != "input":
        shown = json.dumps({**box, key: "\0"})
    else:
        shown = json.dumps("\0")
    words = f"where each x is {kinds(schema.get('items', {}))}" if many else f"where x is {kinds(schema)}"
    return shown.replace(json.dumps("\0"), mark) + " " + words


def handles_in(value) -> list:
    if isinstance(value, dict):
        return [value["$handle"]] if "$handle" in value else [h for v in value.values() for h in handles_in(v)]
    return [h for v in value for h in handles_in(v)] if isinstance(value, list) else []


class Walk:
    def __init__(self, state, decide, pool, cfg, ask, log, check):
        self.state, self.decide, self.pool, self.cfg = state, decide, pool, cfg
        self.ask = ask      # ask(question) -> a value that the schema of the question allows
        self.log = log      # log(record): one decision node
        self.check = check  # check(): raise when a hard limit is reached

    # ---- the root -----------------------------------------------------------------------------

    def root(self) -> tuple:
        """Return ("control", name) or ("operation", entry, capability, input)."""
        entries = self.state.shown()
        hidden = self.cfg.hidden_capabilities
        operations = {e.alias: [self._operation(e, c) for c in [*e.capabilities, *e.actions] if c["name"] not in hidden] for e in entries}
        controls = [Option(f"{name}: {text}", ("control", name), name) for name, text in CONTROLS.items()]
        flat = [o for options in operations.values() for o in options]
        if len(flat) + len(controls) <= len(LABELS):
            choice = self.node("operation", "Choose the next operation for the current intent, or a control option.", flat + controls)
        else:
            # More options than one decision has labels: choose the handle first, then its operation.
            targets = [Option(f"{e.alias}: an operation on this handle", e, e.alias) for e in entries if operations[e.alias]]
            choice = self.node("target", "Choose the handle of the next operation for the current intent, or a control option.", targets + controls)
            if not isinstance(choice, tuple):   # the control options stay: a look at the operations can show that none is needed
                choice = self.node("operation", f"Choose the operation on {choice.alias} for the current intent, or a control option.",
                                   operations[choice.alias] + controls)
        if choice[0] == "control":
            return choice
        _, entry, capability = choice
        context = Context(entry, capability, {"input": None})
        self.fill(capability["input_schema"], [], context.holder, "input", context)
        return "operation", entry, capability, context.holder["input"]

    def _operation(self, entry, capability: dict) -> Option:
        text = f"{entry.alias} {capability['name']}: {capability['description']}"
        if capability in entry.actions:
            text += " (ACTION: it needs a confirmation)"
        return Option(text, ("operation", entry, capability), f"{entry.alias} {capability['name']}")

    def reason(self, control: str) -> str:
        """Why the decision model chose `wake_agent` or `abort`. It is a report, so it never becomes a question:
        the top option stands. The same holds for a decision on the structure of the input (docs/DECISIONS.md, D23)."""
        return self.node("reason", f"You chose {control}. Choose the reason.", [Option(r, r, r) for r in REASONS], abstain=False)

    # ---- one decision -------------------------------------------------------------------------

    def node(self, kind: str, text: str, options: list, *, schema: dict | None = None, context: Context | None = None,
             field: str | None = None, abstain: bool = True, first: int | None = None):
        """One decision among `options`. `schema` is set for an open value: a question can then give any value that it allows.
        `first` is the size of the first page, when the first options are stronger than the others."""
        rest, size = options, first or len(LABELS)
        while True:
            page = rest
            if len(rest) > size:   # show the options in pages
                page = [*rest[:min(size, len(LABELS) - 1)], Option("none of these: show more options", _MORE, "more options")]
            index = self._pass(kind, text, page, context, field, abstain)
            if index is None or page[index].key == NONE_FITS:
                return self._question(kind, options, schema, context, field)
            if page[index].value is not _MORE:
                return page[index].value
            rest, size = rest[len(page) - 1:], len(LABELS)

    def _pass(self, kind, text, options, context, field, abstain) -> int | None:
        if len(options) == 1:   # only one value is possible: no call
            return 0
        view = self.state.view(text)
        t0 = time.perf_counter()
        d = self.decide(view, [o.text for o in options])
        self.state.tokens += d.tokens
        index = judge(d, self.cfg.abstain_top, self.cfg.abstain_ratio, self.cfg.abstain_mass)
        below = index is None   # the decision is below the abstain threshold
        if below and not abstain:
            index = max(range(len(options)), key=lambda i: d.probs[i])
        self.log({"node": kind, "field": field, "capability": context.capability["name"] if context else None,
                  "target": context.entry.alias if context else None, "partial": context.holder["input"] if context else None,
                  "options": [o.key for o in options], "texts": [o.text for o in options],
                  "probs": [round(p, 4) for p in d.probs], "mass": round(d.mass, 4),
                  "choice": None if index is None else options[index].key, "abstained": index is None, "below": below,
                  "tokens": d.tokens, "ms": round((time.perf_counter() - t0) * 1000), "state": view})
        self.check()
        return index

    def _question(self, kind, options, schema, context, field):
        candidates = [{"value": o.key, "text": o.text} for o in options]
        question = {
            "plan": {k: self.state.plan[k] for k in ("intents", "notes") if k in self.state.plan},
            "intent": self.state.plan["intents"][self.state.intent], "node": kind,
            "capability": context.capability["name"] if context else None,
            "target": {"alias": context.entry.alias, "id": context.entry.id} if context else None,
            "field": field, "chosen": context.holder["input"] if context else None, "candidates": candidates,
            "schema": schema or {"enum": [o.key for o in options]},
            "handles": [{k: v for k, v in e.summary(self.cfg.value_chars).items() if k != "schema"} for e in self.state.shown()],
        }
        value = self.ask(question)
        if schema is not None:
            self.pool.answers[self.pool.answer_key(context.entry, context.capability["name"], field)] = value
            return value
        return next(o.value for o in options if o.key == value)

    # ---- the input schema ---------------------------------------------------------------------

    def _field(self, context: Context, path: list, what: str, options: list, schema: dict | None = None, note: str = "", first: int | None = None,
               abstain: bool = True):
        capability = context.capability
        text = (f"Operation: {capability['name']} on {context.entry.alias}. {capability['description']} "
                f"Input so far: {json.dumps(context.holder['input'])}. Choose {what} `{path_text(path)}`.{note}")
        for option in options:
            error = self._failed(context, path, option.value)
            if error:
                option.text += f"  (it was in an input that FAILED: {error})"
        return self.node("field", text, options, schema=schema, context=context, field=path_text(path), first=first, abstain=abstain)

    def _failed(self, context: Context, path: list, value) -> str | None:
        """The error of an earlier input of this operation that had `value` at `path`. A configuration that failed is not a good next choice."""
        for result in reversed(self.state.results):
            error = result.get("error")
            if error and not error.get("retryable") and (result.get("capability"), result.get("target")) == (context.capability["name"], context.entry.alias):
                at = result["input"]
                try:
                    for part in path:
                        at = at[part]
                except (KeyError, IndexError, TypeError):
                    continue
                if at == value and not isinstance(value, (dict, list)):
                    return cut(error.get("message"), 120)
        return None

    def fill(self, schema: dict, path: list, box, key, context: Context) -> None:
        """Put a value that `schema` allows at box[key]. The values chosen so far are in the input already, so each decision sees them."""
        note = f" {schema['description']}" if schema.get("description") else ""
        if "const" in schema:
            box[key] = schema["const"]
        elif "enum" in schema:
            box[key] = self._field(context, path, "the value of", [Option(json.dumps(v), v, v) for v in schema["enum"]], note=note)
        elif "anyOf" in schema or "oneOf" in schema:
            branches = schema.get("anyOf") or schema["oneOf"]
            branch = self._field(context, path, "the form of", [Option(form(b, box, key), b, i) for i, b in enumerate(branches)], note=note)
            self.fill(branch, path, box, key, context)
        elif is_handle_input(schema):
            others = [e for e in self.state.shown() if e is not context.entry]
            options = [Option(f"{e.alias}: {e.handle.info['type']} {e.handle.info['descriptor'].get('name', '')} ({e.origin})", e.id, e.alias) for e in others]
            box[key] = {"$handle": self._field(context, path, "the handle for", options or [Option(NONE_FITS, None, NONE_FITS)],
                                               schema={"type": "string"}, note=note)}
        elif schema.get("type") == "object" and "properties" in schema:
            value = box[key] = {}
            context.properties[id(value)] = schema["properties"]
            for name, sub in schema["properties"].items():
                if name not in schema.get("required", []):
                    hint = f" (when it is left out: {json.dumps(sub['default'])})" if "default" in sub else ""
                    include = self._field(context, [*path, name], "whether the input has the optional field", [
                        Option(f"include it{hint}", True, True), Option("leave it out", False, False)],
                        note=f" {sub['description']}" if sub.get("description") else "", abstain=False)
                    if not include:
                        continue
                self.fill(sub, [*path, name], value, name, context)
        elif schema.get("type") == "array":
            items = box[key] = []
            while "maxItems" not in schema or len(items) < schema["maxItems"]:
                if len(items) >= schema.get("minItems", 0):
                    more = self._field(context, path, f"whether one more item goes in the list (it has {len(items)} now)", [
                        Option("add one more item", True, True), Option("stop: the list is complete", False, False)], note=note, abstain=False)
                    if not more:
                        break
                items.append(None)
                self.fill(schema.get("items", {}), [*path, len(items) - 1], items, len(items) - 1, context)
        elif schema.get("type") == "boolean":
            box[key] = self._field(context, path, "the value of", [Option("true", True, True), Option("false", False, False)], note=note)
        else:
            box[key] = self._open(schema, path, box, context, note)

    def _open(self, schema: dict, path: list, box, context: Context, note: str):
        """An open value: choose from the candidate pool, or `none fits`."""
        name = next((p for p in reversed(path) if isinstance(p, str)), context.capability["name"])
        others = [e for e in map(self.state.entry, handles_in(context.holder["input"])) if e is not None]
        siblings = box if isinstance(box, dict) else {}
        peers = [v for k, v in siblings.items() if context.properties.get(id(box), {}).get(k) == schema and not isinstance(v, (dict, list))]
        found, strong = self.pool.candidates(schema, name, path_text(path), context.capability["name"],
                                             siblings, peers, context.entry, others, self.state)
        options = [Option(f"{json.dumps(value)}  ({label})", value, value) for value, label in found]
        options.append(Option("none fits: no value here is right", None, NONE_FITS))
        return self._field(context, path, "the value of", options, schema=schema, note=note, first=strong or None)
