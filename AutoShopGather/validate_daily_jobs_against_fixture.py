import json
import sys
from pathlib import Path


def normalize_text(value: str) -> str:
    return " ".join(value.strip().split())


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def to_comparable_map(payload: dict, npc_key: str, items_key: str) -> dict:
    result = {}
    for npc in payload.get("npcs", []):
        npc_name = normalize_text(npc[npc_key])
        result[npc_name] = sorted(
            (
                normalize_text(item["item_name_ko"]),
                int(item["nyang_coin_reward"]),
            )
            for item in npc.get(items_key, [])
        )
    return result


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    expected_path = repo_root / "AutoShopGather" / "fixtures" / "daily_jobs_expected_2026-04-12.json"
    actual_path = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else repo_root / "AutoShopGather" / "output" / "live_daily_jobs_normalized.json"
    )

    expected = load_json(expected_path)
    actual = load_json(actual_path)

    expected_map = to_comparable_map(expected, "npc_name_ko", "expected_items")
    actual_map = to_comparable_map(actual, "npc_name_ko", "items")

    ok = True
    for npc_name, expected_items in expected_map.items():
        actual_items = actual_map.get(npc_name)
        if actual_items != expected_items:
            ok = False
            print(f"[MISMATCH] {npc_name}")
            print(f"  expected: {expected_items}")
            print(f"  actual  : {actual_items}")

    extra_npcs = sorted(set(actual_map) - set(expected_map))
    if extra_npcs:
        ok = False
        print(f"[EXTRA_NPCS] {extra_npcs}")

    if ok:
        print("Validation passed against daily_jobs_expected_2026-04-12.json")
    else:
        raise SystemExit(1)
