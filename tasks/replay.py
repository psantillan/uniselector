"""Replay the logged decisions of a run on one or more decision models.

  python -m uniselector_tasks.replay DECISIONS_JSONL EACH MODEL...

The replay takes each decision that abstained, and each decision whose line number less 1 is a
multiple of EACH. For each model it prints one line for each decision: the top option of the log
and the top option of the model, with their probabilities. Then it prints the totals. A top option
that is the same as the log is not a right answer: the replay shows a difference, not a truth.
"""
import json
import os
import sys
import time

from uniselector.config import Config
from uniselector.decide import Decider


def main() -> None:
    cfg = Config(budget_tokens=0, wall_seconds=0)
    rows = [json.loads(line) for line in open(sys.argv[1])]
    picked = [(n, r) for n, r in enumerate(rows, 1) if "node" in r and (r["abstained"] or (n - 1) % int(sys.argv[2]) == 0)]
    for model in sys.argv[3:]:
        decide = Decider(cfg.decision_url, model, os.environ[cfg.decision_key_env])
        same, tokens, cost, times = 0, 0, 0.0, []
        print(f"== {model}")
        for n, r in picked:
            t0 = time.perf_counter()
            # ASSUMED: 60 seconds is the longest that one replayed call can take.
            d = decide({**r["state"], "limits": {**r["state"]["limits"], "elapsed_s": 0, "wall_seconds": 60}}, r["texts"])
            times.append(round((time.perf_counter() - t0) * 1000))
            tokens, cost = tokens + d.tokens, cost + d.cost
            old, new = max(range(len(r["probs"])), key=lambda i: r["probs"][i]), max(range(len(d.probs)), key=lambda i: d.probs[i])
            same += old == new and any(d.probs)
            print(f"D {n} {r['node']} {r['field'] or ''}: log {r['texts'][old][:44]!r} p {r['probs'][old]:.2f}{' ABSTAINED' if r['abstained'] else ''}"
                  f" | model {r['texts'][new][:44]!r} p {d.probs[new]:.2f}{'' if any(d.probs) else ' REFUSED'}")
        print(f"   {len(picked)} decisions, the same top option as the log: {same}, input tokens {tokens}, cost ${cost:.6f}, "
              f"median {sorted(times)[len(times) // 2]} ms, longest {max(times)} ms")


if __name__ == "__main__":
    main()
