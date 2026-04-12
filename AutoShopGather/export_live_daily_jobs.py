import json
import os
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = REPO_ROOT / "of-ps"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

from network.msg_id import MsgId
from proto.net_pb2 import (
    ChangeSceneChannelReq,
    ChangeSceneChannelRsp,
    PlayerMainDataReq,
    ShopInfoReq,
    ShopInfoRsp,
)
from AutoShopGather.live_daily_job_probe import (
    KNOWN_RESPONSE_TYPES,
    GameProbeClient,
    apply_local_log_fallbacks,
    apply_pcsdkui_fallbacks,
    build_player_login_req,
    build_verify_login_req,
    load_env_file,
    parse_int,
)

RESOURCES_DIR = VENDOR_ROOT / "resources" / "data"
OUTPUT_PATH = REPO_ROOT / "AutoShopGather" / "output" / "live_daily_jobs.json"

DEFAULT_ENV = {
    "OF_HOST": "158.179.182.190",
    "OF_PORT": "11001",
    "OF_ACCOUNT_TYPE": "16666",
    "OF_CLIENT_VERSION": "2026-01-28-16-52-36",
    "OF_RESOURCE_VERSION": "2026-04-10-17-44-11",
    "OF_VERSION_NUMBER": "a3e115f690751be4da7d3ab41728568c",
    "OF_DEVICE_MODEL": "MS-7C82 (Micro-Star International Co., Ltd.)",
    "OF_NETWORK": "wifi",
    "OF_OS_NAME": "windows",
    "OF_OS_VER": "Windows 10  (10.0.19045) 64bit",
    "OF_LANGUAGE": "4",
    "OF_OS": "0",
}

NPC_BY_SHOP_ID = {
    2100001: "아즈사",
    2100002: "아야",
    2200001: "리처드",
}

CANONICAL_ITEM_NAMES = {
    5012001: "식초",
    5012002: "밀가루",
    5012003: "면",
    5012004: "후추",
    5012005: "간장",
    5012006: "요리용 맛술",
    5012007: "슬라이스 고기",
    5012008: "채 썬 고기",
    5012009: "케첩",
    5012010: "카레",
    5012011: "쌀밥",
    5013001: "매콤새콤 미역무침",
    5013004: "농어 맑은탕",
    5013005: "마늘 메기 조림",
    5013006: "오징어 볶음면",
    5013007: "간장 계란밥",
    5013010: "잉어 탕수조림",
    5013011: "송어 매운탕",
    5013012: "사과파이",
    5013013: "가물치 채소말이",
    5013014: "야채 샐러드",
    5013016: "버섯 구이",
    5013018: "쫀득한 찰광어찜",
    5013019: "계란 볶음밥",
    5013021: "청어 간장조림",
    5013022: "부드러운 연어회",
    5013023: "매쉬드 포테이토",
    5013024: "마늘 석화구이",
    5013025: "생선 꼬치구이",
    5013026: "미소 된장국",
    5013027: "제철 열빙어찜",
    5013028: "장어 덮밥",
    5013029: "편어 간장조림",
    5013030: "문어 숙회",
    5013031: "소라 숙회",
    5013032: "생강소스 꽁치구이",
    5013034: "바삭바삭 에그롤",
    5013035: "먹물 리조또",
    5013036: "갯농어 국밥",
    5013037: "해산물 파스타",
    5013038: "틸라피아 조림",
    5013040: "정어리 통조림",
    5013041: "수르스트뢰밍",
    5013042: "갈치 구이",
    5013043: "타코야끼",
    5013044: "종이호일 생선구이",
    5013045: "짭짤한 정어리 튀김",
    5013046: "진득한 고등어죽",
    5013047: "황금새우볼",
    5013048: "철갑상어 고추찜",
    5013049: "해산물 샐러드",
    5013050: "마늘 멸치 샐러드",
    5013051: "가리비 그라탕",
    5013053: "따끈따끈 해물죽",
    5013054: "크림 홍합 오븐구이",
    5013055: "무지개송어찜",
    5013056: "담백한 삼치 조림",
    5013057: "성게알 초밥",
    5013058: "병어 구이",
    5013059: "연어 숯불구이",
    5013060: "크림 게살볶음",
    5013061: "생선 카레",
    5013062: "레몬 우럭구이",
    5013063: "쥐치 조림",
    5013064: "참치 초밥",
    5013065: "스페셜 모둠회",
    5013067: "해산물 크림수프",
    5013068: "마라 생선볶음",
    5013069: "아귀찜",
    5013070: "순살 황새치 강정",
    5013071: "특제 랍스터 토스트",
    5013072: "아슬아슬 복어회",
    5013073: "새우, 게 소금구이",
    5013075: "주걱철갑상어 튀김",
    5013076: "매콤 가오리찜",
    5033001: "모자 샘플",
    5033002: "두건 샘플",
    5033003: "머리띠 샘플",
    5033004: "안경 샘플",
    5033005: "망토 샘플",
    5033006: "외투 샘플",
    5033007: "넥타이 샘플",
    5033008: "초커 샘플",
    5033009: "스카프 샘플",
    5033010: "상의 샘플",
    5033011: "손목 커버 샘플",
    5033012: "팔찌 샘플",
    5033013: "장갑 샘플",
    5033014: "바지 샘플",
    5033015: "치마 샘플",
    5033016: "양말 샘플",
    5033017: "신발 샘플",
    5033018: "파우치 샘플",
}



def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def build_string_map(resources_dir: Path) -> dict[int, str]:
    rows = load_json(resources_dir / "String_Korea.json")["string_item_text"]["datas"]
    return {
        row["i_d"]: (row.get("text") or [""])[0]
        for row in rows
        if isinstance(row, dict) and row.get("i_d") is not None
    }


def build_item_name_map(resources_dir: Path, string_map: dict[int, str]) -> tuple[dict[int, str], dict[int, dict]]:
    item_rows = load_json(resources_dir / "Item.json")["item"]["datas"]
    item_name_map: dict[int, str] = {}
    item_meta: dict[int, dict] = {}
    for row in item_rows:
        item_id = row.get("i_d")
        if item_id is None:
            continue
        item_name_map[item_id] = string_map.get(row.get("text_i_d"), "")
        item_meta[item_id] = row
    return item_name_map, item_meta


def build_shop_reward_map(resources_dir: Path) -> dict[int, dict[int, int]]:
    shop_rows = load_json(resources_dir / "Shop.json")["pool"]["datas"]
    reward_map: dict[int, dict[int, int]] = {}
    for row in shop_rows:
        pool_id = row.get("i_d")
        if pool_id not in {2100001, 2100002, 2100003, 2200001}:
            continue
        reward_map[pool_id] = {
            item["index"]: item["item_num"]
            for item in row.get("items", [])
            if item.get("index") is not None and item.get("item_num") is not None
        }
    return reward_map


def build_candidate_lists(resources_dir: Path, item_meta: dict[int, dict]) -> dict[int, list[int]]:
    cooking_rows = load_json(resources_dir / "Cooking.json")["cook_food"]["datas"]
    spin_rows = load_json(resources_dir / "Spin.json")["spin_item"]["datas"]

    cooking_ids = [
        row["get_item_i_d"]
        for row in cooking_rows
        if row.get("get_item_i_d") in item_meta
    ]
    spin_tag8_ids = [
        row["get_item_i_d"]
        for row in spin_rows
        if row.get("get_item_i_d") in item_meta
        and item_meta[row["get_item_i_d"]].get("new_bag_item_tag") == 8
    ]

    candidate_lists = {
        2100003: cooking_ids[0:11],
        2100001: cooking_ids[11:19],
        2100002: cooking_ids[18:87],
        2200001: spin_tag8_ids[20:38],
    }

    expected_lengths = {
        2100003: 11,
        2100001: 8,
        2100002: 69,
        2200001: 18,
    }
    for pool_id, expected_length in expected_lengths.items():
        actual_length = len(candidate_lists[pool_id])
        if actual_length != expected_length:
            raise RuntimeError(
                f"candidate list length mismatch for pool {pool_id}: "
                f"expected {expected_length}, got {actual_length}"
            )

    return candidate_lists


def display_name(item_id: int, raw_name: str) -> str:
    return CANONICAL_ITEM_NAMES.get(item_id, raw_name)


def build_env() -> dict:
    env_path = REPO_ROOT / ".overfield-live.env"
    env = dict(os.environ)
    env.update(load_env_file(env_path))
    for key, value in DEFAULT_ENV.items():
        if not env.get(key):
            env[key] = value
    env = apply_pcsdkui_fallbacks(REPO_ROOT, env)
    env = apply_local_log_fallbacks(env)
    return env


def send_and_expect(client: GameProbeClient, msg_id: int, req, timeout_seconds: float = 5.0) -> dict:
    packet_id = client.send_proto(msg_id, req)
    frame, frames = client.recv_until_packet_id(packet_id, timeout_seconds)
    if frame is None:
        raise RuntimeError(f"timed out waiting for response to msg_id={msg_id}, frames={len(frames)}")
    return frame


def maybe_change_scene(client: GameProbeClient, scene_id: int = 1) -> dict:
    req = ChangeSceneChannelReq()
    req.scene_id = scene_id
    frame = send_and_expect(client, MsgId.ChangeSceneChannelReq, req)
    client.drain_for(0.5)
    return frame


def fetch_live_shop_rows() -> dict:
    KNOWN_RESPONSE_TYPES[MsgId.ChangeSceneChannelRsp] = ChangeSceneChannelRsp
    KNOWN_RESPONSE_TYPES[MsgId.ShopInfoRsp] = ShopInfoRsp

    env = build_env()
    host = env.get("OF_HOST") or env.get("OF_GATE_TCP_IP") or env.get("gate_tcp_ip")
    port = parse_int(env, "OF_PORT", 0) or parse_int(env, "OF_GATE_TCP_PORT", 0)
    if not host or not port:
        raise RuntimeError("missing live gate host/port in env or local logs")

    client = GameProbeClient(host, port)
    try:
        verify_frame = send_and_expect(client, MsgId.VerifyLoginTokenReq, build_verify_login_req(env))
        if (verify_frame.get("parsed") or {}).get("status") != 1:
            raise RuntimeError(f"verify login failed: {verify_frame}")

        login_frame = send_and_expect(client, MsgId.PlayerLoginReq, build_player_login_req(env))
        if (login_frame.get("parsed") or {}).get("status") != 1:
            raise RuntimeError(f"player login failed: {login_frame}")

        main_req = PlayerMainDataReq()
        main_frame = send_and_expect(client, MsgId.PlayerMainDataReq, main_req)
        if (main_frame.get("parsed") or {}).get("status") != 1:
            raise RuntimeError(f"player main data failed: {main_frame}")

        change_scene_frame = maybe_change_scene(client, 1)

        shop_frames = {}
        for shop_id in NPC_BY_SHOP_ID:
            req = ShopInfoReq()
            req.shop_id = shop_id
            shop_frames[shop_id] = send_and_expect(client, MsgId.ShopInfoReq, req)

        return {
            "verify": verify_frame,
            "login": login_frame,
            "main": main_frame,
            "change_scene": change_scene_frame,
            "shops": shop_frames,
        }
    finally:
        client.close()


def decode_live_jobs() -> dict:
    string_map = build_string_map(RESOURCES_DIR)
    item_name_map, item_meta = build_item_name_map(RESOURCES_DIR, string_map)
    reward_map = build_shop_reward_map(RESOURCES_DIR)
    candidate_lists = build_candidate_lists(RESOURCES_DIR, item_meta)
    live = fetch_live_shop_rows()

    npcs = []
    for shop_id, npc_name in NPC_BY_SHOP_ID.items():
        parsed = live["shops"][shop_id].get("parsed") or {}
        if parsed.get("status") != 1:
            raise RuntimeError(f"shop {shop_id} failed: {parsed}")

        items = []
        for grid in parsed.get("grids", []):
            pool_id = int(grid["pool_id"])
            pool_index = int(grid["pool_index"])
            relative_index = pool_index % 100
            candidate_item_ids = candidate_lists[pool_id]
            if relative_index >= len(candidate_item_ids):
                raise RuntimeError(
                    f"pool index out of range: shop={shop_id} pool={pool_id} "
                    f"pool_index={pool_index} relative_index={relative_index} "
                    f"candidate_count={len(candidate_item_ids)}"
                )

            item_id = candidate_item_ids[relative_index]
            raw_name = item_name_map.get(item_id, "")
            reward = reward_map.get(pool_id, {}).get(pool_index)

            items.append(
                {
                    "grid_id": int(grid["grid_id"]),
                    "pool_id": pool_id,
                    "pool_index": pool_index,
                    "item_id": item_id,
                    "item_name": display_name(item_id, raw_name),
                    "nyang_coin_reward": reward,
                }
            )

        items.sort(key=lambda row: row["grid_id"])
        npcs.append(
            {
                "npc_name": npc_name,
                "shop_id": shop_id,
                "items": items,
            }
        )

    payload = {
        "date": str(date.today()),
        "npcs": npcs,
        "live_meta": {
            "verify_status": (live["verify"].get("parsed") or {}).get("status"),
            "login_status": (live["login"].get("parsed") or {}).get("status"),
            "main_status": (live["main"].get("parsed") or {}).get("status"),
            "change_scene_status": (live["change_scene"].get("parsed") or {}).get("status"),
            "change_scene_id": (live["change_scene"].get("parsed") or {}).get("scene_id"),
        },
    }
    return payload





def main() -> None:
    payload = decode_live_jobs()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nsaved: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
