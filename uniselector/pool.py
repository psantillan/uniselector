"""The candidate pool: the values that a decision can choose for an open field.

The pool collects values from the state. It does not know any resource type:
- values observed on a handle: its descriptor, and a bounded probe (each capability that reads,
  gives a value, and needs no input). The pool caches the probes by handle version.
- literals in the plan's intents and notes: the quoted strings and the numbers, and also each word
- values in earlier results
- the default of the schema
- answers that a sub-agent or the main agent gave earlier in this run
The pool keeps the values that the schema of the field allows, puts them in an order, and gives the
first ones. docs/DECISIONS.md (D8) gives the order and its reason.
"""
import json
import re

from .plan import violations

NONE_FITS = "none fits"
# One decision has 20 labels (decide.LABELS). One label is for `none fits`.
MAX_CANDIDATES = 19
# ASSUMED: two places tell the decision where a value comes from. More places make the option long.
MAX_PLACES = 2
# ASSUMED: four values show what a place holds.
MAX_SAMPLE = 4

_QUOTED = re.compile(r"'([^']+)'|\"([^\"]+)\"")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.]*\w)")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def leaves(value, path=()):
    """Each scalar in a JSON value, with the keys on the way to it. A table (`columns` and `rows` of lists) gives the column name as a key."""
    if isinstance(value, dict):
        columns, rows = value.get("columns"), value.get("rows")
        if isinstance(columns, list) and isinstance(rows, list) and all(isinstance(r, list) and len(r) == len(columns) for r in rows):
            for row in rows:
                for column, item in zip(columns, row):
                    yield from leaves(item, path + ("rows", str(column)))
            value = {k: v for k, v in value.items() if k != "rows"}
        for key, item in value.items():
            yield from leaves(item, path + (str(key),))
    elif isinstance(value, list):
        for item in value:
            yield from leaves(item, path)
    elif value is not None:
        yield value, path


def literals(text: str) -> list:
    """The values that a sentence states: its quoted strings and its numbers."""
    quoted = [a or b for a, b in _QUOTED.findall(text)]
    return [*quoted, *(float(n) if "." in n else int(n) for n in _NUMBER.findall(text))]


def words(text: str) -> list:
    return _WORD.findall(text)


def _stem(word: str) -> str:
    word = word.lower()
    return word[:-1] if word.endswith("s") else word


def can_probe(capability: dict, hidden: list) -> bool:
    return (capability["name"] not in hidden and capability["returns"] == "value" and capability["execution"]["mode"] == "sync"
            and capability["execution"]["idempotent"] and not capability["effects"]["write"] and not capability["effects"]["external"]
            and not capability["input_schema"].get("required"))


class Pool:
    def __init__(self, probe, hidden: list):
        self.probe = probe          # probe(entry, capability name) -> the output value, or None
        self.hidden = hidden
        self._observed = {}         # (handle id, version) -> [(value, path, label)]
        self._outputs = {}          # (handle id, version) -> {capability name: the output of its probe}
        self.answers = {}           # (handle id, version, capability, field path) -> value

    def _probed(self, entry) -> tuple:
        """Probe a handle at its version now, one time for each version. Return the key of the version."""
        key = (entry.id, entry.handle.observe().get("version"))
        if key not in self._observed:
            found = [(v, p, f"{entry.alias} descriptor") for v, p in leaves(entry.handle.info["descriptor"].get("schema"))]
            outputs = {}
            for capability in entry.capabilities:
                if can_probe(capability, self.hidden):
                    outputs[capability["name"]] = self.probe(entry, capability["name"])
                    found += [(v, p, f"{entry.alias} {capability['name']}") for v, p in leaves(outputs[capability["name"]])]
            for cache, value in ((self._observed, found), (self._outputs, outputs)):
                for old in [k for k in cache if k[0] == entry.id]:   # an earlier version of a stream is of no use
                    del cache[old]
                cache[key] = value
        return key

    def observed(self, entry) -> list:
        return self._observed[self._probed(entry)]

    def newest(self, entry) -> dict:
        """The outputs of the probes of a handle at its version now: what a look at the handle shows."""
        return {name: output for name, output in self._outputs[self._probed(entry)].items() if output is not None}

    def answer_key(self, entry, capability: str, field: str) -> tuple:
        return (entry.id, entry.handle.observe().get("version"), capability, re.sub(r"\[\d+\]", "[]", field))

    def candidates(self, schema: dict, name: str, field: str, capability: str, siblings: dict, peers: list, target, others: list, state) -> tuple:
        """Return ([(value, label)], strong): at most MAX_CANDIDATES values that `schema` allows, best first.

        The first `strong` values have evidence for this field: an answer, the default, a value that the current intent
        states, or a value from a place that the field or an earlier choice names. The other values are weak: they only
        have the right type.

        `peers` are the values of the sibling fields that have the same schema as this field (`left` and `right` of a
        join key). A peer is the same kind of thing as this field, so the places of a peer are places for this field.
        A peer does not name a place of values for this field (docs/DECISIONS.md, D22)."""
        chosen = {v for v in siblings.values() if isinstance(v, str)} - set(peers)
        alike = {path for entry in [target, *others] for value, path, _ in self.observed(entry) if path and value in peers}
        found = []

        def add(value, path, label, tier):
            if violations(schema, value):
                return
            # A value from a place that an earlier choice names (the chosen column) comes first.
            # A value from a place with the name of the field (a column name for `column`) comes next.
            score = (2 if chosen & set(path) or path in alike else 0) + (1 if any(_stem(name) in _stem(k) or _stem(k) in _stem(name) for k in path) else 0)
            found.append((not (score or tier <= 2), -score, tier, len(found), value, label, path))

        this = self.answer_key(target, capability, field)
        for key, value in self.answers.items():
            add(value, (), "an earlier answer", 0 if key == this else 5)
        if "default" in schema:
            add(schema["default"], (), "the default", 1)
        intents = state.plan["intents"]
        for value in literals(intents[state.intent]):
            add(value, (), "stated in the current intent", 2)
        for tier, entry in [(3, target), *((4, e) for e in others)]:
            for value, path, label in self.observed(entry):
                add(value, path, label, tier)
        for value in words(intents[state.intent]):
            add(value, (), "a word of the current intent", 5)
        for output in reversed(state.outputs):
            for value, path in leaves(output):
                add(value, path, "in an earlier result", 5)
        for text in [*intents[:state.intent], *intents[state.intent + 1:], state.plan.get("notes", "")]:
            for value in [*literals(text), *words(text)]:
                add(value, (), "in the plan", 6)
        # One value can come from more than one place. The decision sees each place: a word of the intent that is
        # also a value of a column is not the name of a column.
        places = {}
        for weak, _, _, _, value, label, path in sorted(found, key=lambda f: f[:4]):
            place = places.setdefault(json.dumps(value), {"value": value, "weak": weak, "labels": [], "paths": []})
            if path not in place["paths"] or not path and label not in place["labels"]:   # the same place from two sources is one place
                place["paths"].append(path)
                place["labels"].append(label + (": " + " > ".join(path) if path else ""))
        # A candidate can be the name of a place (a column). The decision then sees some of the values at that place.
        at, wanted = {}, [json.dumps(v) for v in [*literals(intents[state.intent]), *words(intents[state.intent])]]
        for entry in [target, *others]:
            for value, path, _ in self.observed(entry):
                if path and json.dumps(value) not in at.setdefault(path[-1], []):
                    at[path[-1]].append(json.dumps(value))
        out = []
        for place in list(places.values())[:MAX_CANDIDATES]:
            value, label = place["value"], "; ".join(place["labels"][:MAX_PLACES])
            if isinstance(value, str) and value in at:
                sample = sorted(at[value], key=lambda v: v not in wanted)[:MAX_SAMPLE]   # a value that the intent names comes first
                label += f". Values at {value}: " + ", ".join(sample)
            out.append((value, label))
        return out, sum(not place["weak"] for place in list(places.values())[:MAX_CANDIDATES])
