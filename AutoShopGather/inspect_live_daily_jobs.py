from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = REPO_ROOT / "of-ps"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

from proto.net_pb2 import (
    ActivityQuestDataNotice,
    ActivityRegularDataNotice,
    PackNotice,
    PlayerMainDataReq,
    ShopInitNotice,
)
from AutoShopGather.live_daily_job_probe import (
    GameProbeClient,
    apply_local_log_fallbacks,
    apply_pcsdkui_fallbacks,
    build_player_login_req,
    build_verify_login_req,
    load_env_file,
    message_to_json_dict,
)


NOTICE_TYPES = {
    "PackNotice": PackNotice,
    "ShopInitNotice": ShopInitNotice,
    "ActivityQuestDataNotice": ActivityQuestDataNotice,
    "ActivityRegularDataNotice": ActivityRegularDataNotice,
}

NPC_PREFIXES = {
    "aya": 4012,
    "richard": 4015,
    "azusa": 4016,
}


def load_resource_strings(path: Path) -> dict[int, str]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    out: dict[int, str] = {}

    def walk(node) -> None:
        if isinstance(node, dict):
            if "i_d" in node:
                text = None
                if "text" in node:
                    raw = node["text"]
                    if isinstance(raw, list) and raw and isinstance(raw[0], str):
                        text = raw[0]
                    elif isinstance(raw, str):
                        text = raw
                elif isinstance(node.get("value"), str):
                    text = node["value"]
                elif isinstance(node.get("name"), str):
                    text = node["name"]
                if text is not None:
                    out.setdefault(int(node["i_d"]), text)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(obj)
    return out


def index_datas(path: Path, required_key: str | None = None) -> dict[int, dict]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    out: dict[int, dict] = {}
    for value in obj.values():
        if not isinstance(value, dict) or "datas" not in value:
            continue
        for entry in value["datas"]:
            if not isinstance(entry, dict) or "i_d" not in entry:
                continue
            if required_key is not None and required_key not in entry:
                continue
            out[int(entry["i_d"])] = entry
    return out


def item_name(item_idx: dict[int, dict], str_map: dict[int, str], item_id: int) -> str | None:
    item = item_idx.get(item_id)
    if item is not None and item.get("text_i_d") in str_map:
        return str_map[item["text_i_d"]]
    return str_map.get(item_id)


def build_static_catalog(resources_dir: Path) -> dict[str, list[dict]]:
    str_map = load_resource_strings(resources_dir / "String_Korea.json")
    item_idx = index_datas(resources_dir / "Item.json")
    quest_idx = index_datas(resources_dir / "Quest.json", "quest_group")
    cond_idx = index_datas(resources_dir / "Quest.json", "quest_condition_set")
    achieve_idx = index_datas(resources_dir / "Achieve.json")
    reward_idx = index_datas(resources_dir / "Reward.json", "reward_item_pool_group")

    catalog: dict[str, list[dict]] = {}
    for npc_name, prefix in NPC_PREFIXES.items():
        rows: list[dict] = []
        for quest_id in sorted(qid for qid in quest_idx if str(qid).startswith(str(prefix))):
            quest = quest_idx[quest_id]
            cond_ids = [
                cond_id
                for cond_set in cond_idx.get(quest_id, {}).get("quest_condition_set", [])
                for cond_id in cond_set.get("achieve_condition_i_d", [])
            ]
            need = []
            for cond_id in cond_ids:
                cond = achieve_idx.get(cond_id, {})
                for item_id in cond.get("param", []):
                    need.append(
                        {
                            "condition_id": cond_id,
                            "item_id": item_id,
                            "item_name": item_name(item_idx, str_map, item_id),
                            "count": cond.get("count_param"),
                        }
                    )
            rewards = []
            for reward in reward_idx.get(quest.get("reward"), {}).get("reward_item_pool_group", []):
                reward_item_id = reward.get("item_i_d")
                rewards.append(
                    {
                        "item_id": reward_item_id,
                        "item_name": item_name(item_idx, str_map, reward_item_id),
                        "count": reward.get("item_min_count"),
                    }
                )
            rows.append(
                {
                    "quest_id": quest_id,
                    "quest_name": str_map.get(quest.get("name")),
                    "needed": need,
                    "rewards": rewards,
                }
            )
        catalog[npc_name] = rows
    return catalog


def load_fixture(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def parse_known_notice(frame: dict) -> dict | None:
    msg_type = NOTICE_TYPES.get(frame["msg_name"])
    if msg_type is None or not frame.get("raw_b64"):
        return frame.get("parsed")
    message = msg_type()
    message.ParseFromString(base64.b64decode(frame["raw_b64"]))
    return message_to_json_dict(message)


def fixture_rows_by_npc(fixture: dict | None) -> dict[str, list[dict]]:
    if not fixture:
        return {}
    out: dict[str, list[dict]] = {}
    rows = fixture.get("npcs")
    if rows is None and isinstance(fixture.get("items"), list):
        rows = fixture["items"]
    if rows is None and isinstance(fixture.get("rows"), list):
        rows = fixture["rows"]
    if not isinstance(rows, list):
        return out
    for npc in rows:
        if not isinstance(npc, dict):
            continue
        npc_name = npc.get("npc_name") or npc.get("npc") or npc.get("name") or "unknown"
        out[npc_name] = npc.get("items", []) if isinstance(npc.get("items"), list) else [npc]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect live OverField daily-job state for the current account.")
    parser.add_argument(
        "--env",
        default=".overfield-live.env",
        help="Local env file. Defaults to .overfield-live.env in repo root.",
    )
    parser.add_argument(
        "--resources-dir",
        default="of-ps/resources/data",
        help="Path to extracted resource JSON directory.",
    )
    parser.add_argument(
        "--fixture",
        default="AutoShopGather/fixtures/daily_jobs_expected_2026-04-12.json",
        help="Optional expected daily-job fixture for comparison.",
    )
    parser.add_argument(
        "--output",
        default="AutoShopGather/output/live_daily_job_state.json",
        help="Where to write the diagnostic JSON.",
    )
    args = parser.parse_args()

    repo_root = REPO_ROOT
    env = load_env_file((repo_root / args.env).resolve())
    env = apply_pcsdkui_fallbacks(repo_root, env)
    env = apply_local_log_fallbacks(env)

    fixture = load_fixture((repo_root / args.fixture).resolve())
    static_catalog = build_static_catalog((repo_root / args.resources_dir).resolve())

    client = GameProbeClient(env["OF_HOST"], int(env["OF_PORT"]))
    try:
        packet_id = client.send_proto(1001, build_verify_login_req(env))
        verify_frame, _ = client.recv_until_packet_id(packet_id, 5)

        packet_id = client.send_proto(1003, build_player_login_req(env))
        login_frame, _ = client.recv_until_packet_id(packet_id, 5)

        packet_id = client.send_proto(1005, PlayerMainDataReq())
        main_frame, frames = client.recv_until_packet_id(packet_id, 8)
        frames += client.drain_for(2.0)
    finally:
        client.close()

    parsed_main = main_frame["parsed"] or {}
    quest_rows = parsed_main.get("quest_detail", {}).get("quests", [])
    active_daily_quests = [
        quest
        for quest in quest_rows
        if quest.get("quest_id")
        and str(quest["quest_id"]).startswith(("4012", "4015", "4016"))
    ]

    notices = {}
    for frame in frames:
        name = frame["msg_name"]
        if name in NOTICE_TYPES:
            notices[name] = parse_known_notice(frame)

    summary = {
        "verify_status": (verify_frame or {}).get("parsed", {}).get("status"),
        "login_status": (login_frame or {}).get("parsed", {}).get("status"),
        "main_status": parsed_main.get("status"),
        "account": {
            "player_id": parsed_main.get("player_id"),
            "player_name": parsed_main.get("player_name"),
            "level": parsed_main.get("level"),
            "scene_id": parsed_main.get("scene_id"),
            "unlock_functions": parsed_main.get("unlock_functions", []),
        },
        "live": {
            "quest_count": len(quest_rows),
            "active_daily_quest_rows": active_daily_quests,
            "quest_condition_only_hits": [
                quest
                for quest in quest_rows
                if any(
                    str(cond.get("condition_id", "")).startswith(("20010001", "20010002", "20010004"))
                    for cond in quest.get("conditions", [])
                )
            ],
            "shop_init_notice": notices.get("ShopInitNotice"),
            "activity_quest_notice": notices.get("ActivityQuestDataNotice"),
            "activity_regular_notice": notices.get("ActivityRegularDataNotice"),
        },
        "static_catalog": static_catalog,
        "fixture": fixture,
        "fixture_rows_by_npc": fixture_rows_by_npc(fixture),
        "diagnosis": {
            "has_live_daily_quests": bool(active_daily_quests),
            "shop_init_has_player_shop": bool((notices.get("ShopInitNotice") or {}).get("player_shop")),
            "likely_blocker": None,
        },
    }

    if not active_daily_quests:
        summary["diagnosis"]["likely_blocker"] = (
            "Current account/session does not expose 4012/4015/4016 daily-job quests in PlayerMainDataRsp. "
            "Most likely the feature is not unlocked on this account yet, or the daily-job state is only returned "
            "after entering the relevant map / unlocking the NPC system."
        )

    output_path = (repo_root / args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote live daily-job diagnostic to: {output_path}")


if __name__ == "__main__":
    main()
