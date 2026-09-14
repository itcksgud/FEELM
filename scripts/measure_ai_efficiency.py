"""Measure frozen text bundles or explicit Codex rollout usage, without model calls."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path

FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
          "output_tokens", "reasoning_output_tokens", "total_tokens")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def measure_bundle(manifest_path: Path) -> dict:
    import tiktoken
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    encoding = tiktoken.get_encoding(manifest["encoding"])
    results = {}
    for variant, bundle in manifest["bundles"].items():
        packet = Path(bundle["packet_path"])
        if not packet.is_absolute():
            packet = manifest_path.parent / packet
        text = packet.read_text(encoding="utf-8-sig")
        digest = sha256(text.encode("utf-8"))
        if digest != bundle["sha256_normalized_packet"]:
            raise ValueError(f"Frozen packet changed: {variant}")
        results[variant] = {
            "tokens": len(encoding.encode(text, disallowed_special=())),
            "characters": len(text), "lines": len(text.splitlines()),
            "sha256_normalized_packet": digest,
        }
    before = results["before"]["tokens"]
    if before <= 0:
        raise ValueError("Before bundle must have positive tokens")
    return {
        "measurement": "local_text_token_proxy",
        "tokenizer": "tiktoken", "version": importlib.metadata.version("tiktoken"),
        "encoding": manifest["encoding"], "manifest_sha256": sha256(manifest_path.read_bytes()),
        "bundles": results,
        "reduction_fraction": 1 - results["after"]["tokens"] / before,
        "limitations": [
            "Frozen task-specific text only; excludes tool/system framing and model outputs.",
            "This encoding is an explicit proxy, not a claim about Astra's internal tokenizer.",
            "No claim of realized full-task token, credit, subscription or cash savings.",
        ],
    }


def read_rollout(path: Path) -> dict:
    """Use cumulative increments, skipping repeated counters, not token-event sums."""
    previous = {key: 0 for key in FIELDS}
    counts = {key: 0 for key in FIELDS}
    increments = 0
    duplicates = 0
    first = last = None
    session_id = None
    model_contexts = set()
    tool_calls = 0
    malformed = 0
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        payload = event.get("payload", {})
        kind = event.get("type")
        if kind == "session_meta":
            session_id = payload.get("id") or payload.get("session_id")
        if kind == "turn_context":
            model_contexts.add((payload.get("model"), payload.get("effort")))
        if kind == "response_item" and payload.get("type") in ("function_call", "custom_tool_call"):
            tool_calls += 1
        if kind != "event_msg" or payload.get("type") != "token_count":
            continue
        info = payload.get("info")
        if not info or not info.get("total_token_usage"):
            continue
        raw = info["total_token_usage"]
        required = ("input_tokens", "cached_input_tokens", "output_tokens",
                    "reasoning_output_tokens", "total_tokens")
        if any(key not in raw for key in required):
            raise ValueError("Missing usage breakdown; unavailable is not zero")
        current = {key: raw.get(key, 0) for key in FIELDS}
        if any(not isinstance(value, int) or value < 0 for value in current.values()):
            raise ValueError("Invalid usage counters")
        if current["total_tokens"] != current["input_tokens"] + current["output_tokens"]:
            raise ValueError("Input/output/total counters do not reconcile")
        if current["cached_input_tokens"] > current["input_tokens"]:
            raise ValueError("Cached tokens exceed input")
        if current["reasoning_output_tokens"] > current["output_tokens"]:
            raise ValueError("Reasoning tokens exceed output")
        if any(current[key] < previous[key] for key in FIELDS):
            raise ValueError("Cumulative counter decreased; split/reset attribution needs review")
        if current == previous:
            duplicates += 1
            continue
        for key in FIELDS:
            counts[key] += current[key] - previous[key]
        previous = current
        increments += 1
        timestamp = event.get("timestamp")
        first = first or timestamp
        last = timestamp
    if malformed:
        raise ValueError(f"{malformed} malformed JSON lines; wait for a completed log or repair a copy")
    if not increments:
        raise ValueError("No usage counters in rollout")
    return {
        "path": str(path), "sha256": sha256(path.read_bytes()), "session_id": session_id,
        "first_usage_utc": first, "last_usage_utc": last,
        "contexts": sorted([list(pair) for pair in model_contexts], key=str),
        "usage": counts, "usage_updates": increments, "duplicate_snapshots_skipped": duplicates,
        "tool_calls": tool_calls,
    }


def aggregate_rollouts(paths: list[Path]) -> dict:
    resolved = [path.resolve() for path in paths]
    if len(set(resolved)) != len(resolved):
        raise ValueError("Duplicate rollout path")
    rows = [read_rollout(path) for path in resolved]
    # Multiple rollout segments may share a session; caller must not overlap inherited history.
    return {
        "measurement": "observed_rollout_counters",
        "rollouts": rows,
        "usage": {key: sum(row["usage"][key] for row in rows) for key in FIELDS},
        "scope": "Only explicitly supplied, nonoverlapping completed log files; include all child runs and retries.",
        "limitations": ["Caller must verify log coverage and no inherited overlapping history.",
                        "Cumulative reasoning is already in output, cached input already in input.",
                        "Does not infer prices, cash spending, quality, or causal savings."],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    bundle = sub.add_parser("bundle")
    bundle.add_argument("--manifest", required=True, type=Path)
    bundle.add_argument("--output", required=True, type=Path)
    usage = sub.add_parser("usage")
    usage.add_argument("--rollout", required=True, type=Path, action="append")
    usage.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = measure_bundle(args.manifest) if args.command == "bundle" else aggregate_rollouts(args.rollout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "measurement": result["measurement"]}))


if __name__ == "__main__":
    main()
