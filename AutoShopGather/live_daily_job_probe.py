import base64
import json
import re
import socket
import struct
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = REPO_ROOT / "of-ps"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

try:
    import snappy_py as snappy
except ImportError:
    import snappy

from google.protobuf.json_format import MessageToDict

from network.msg_id import MsgId
from proto.net_pb2 import (
    AccountOSType,
    LanguageType,
    NpcTalkReq,
    PacketHead,
    PlayerLoginReq,
    PlayerMainDataReq,
    PlayerMainDataRsp,
    PlayerLoginRsp,
    QuestNotice,
    VerifyLoginTokenReq,
    VerifyLoginTokenRsp,
    NpcTalkRsp,
)


HEADER_STRUCT = struct.Struct(">H")
NOSEQ_COMMANDS = frozenset((1002, 1004, 1006, 1008))

ID_TO_NAME = {
    value: key
    for key, value in vars(MsgId).items()
    if not key.startswith("__") and isinstance(value, int)
}

KNOWN_RESPONSE_TYPES = {
    MsgId.VerifyLoginTokenRsp: VerifyLoginTokenRsp,
    MsgId.PlayerLoginRsp: PlayerLoginRsp,
    MsgId.PlayerMainDataRsp: PlayerMainDataRsp,
    MsgId.NpcTalkRsp: NpcTalkRsp,
    MsgId.QuestNotice: QuestNotice,
}


def load_pcsdkui_auth_state(repo_root: Path) -> dict:
    from AutoShopGather.extract_pcsdkui_auth_state import extract_state

    candidate_dirs = [
        repo_root / "pcsdkui_storage" / "Local Storage" / "leveldb",
        repo_root / "of-ps" / "pcsdkui_storage" / "Local Storage" / "leveldb",
    ]
    for leveldb_dir in candidate_dirs:
        if leveldb_dir.exists():
            return extract_state(leveldb_dir)
    return {}


def load_qgpc_login_state() -> dict:
    log_path = Path(r"C:\Program Files (x86)\Steam\steamapps\common\OverField\launcher_Data\Quicksdk\qgpc_log.txt")
    if not log_path.exists():
        return {}

    matches = []
    for raw_line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "OnMessageReceived:" not in raw_line or '"action":"loginSuccess"' not in raw_line:
            continue
        json_start = raw_line.find("{")
        if json_start < 0:
            continue
        try:
            payload = json.loads(raw_line[json_start:])
        except json.JSONDecodeError:
            continue
        params = payload.get("params", {})
        if isinstance(params, dict) and params.get("uid") and params.get("userToken"):
            matches.append(params)

    if not matches:
        return {}

    latest = matches[-1]
    return {
        "uid": str(latest.get("uid", "")),
        "username": str(latest.get("username", "")),
        "user_token": str(latest.get("userToken", "")),
        "auth_token": str(latest.get("authToken", "")),
        "timeleft": str(latest.get("timeleft", "")),
    }


def load_player_log_state() -> dict:
    player_log = Path.home() / "AppData" / "LocalLow" / "Inutan" / "OverField" / "Player.log"
    if not player_log.exists():
        return {}

    state: dict[str, str] = {}
    patterns = {
        "base_url": re.compile(r"baseUrl:\s*(\S+)"),
        "dispatch_url": re.compile(r"getClientVersionAddress:\s*(\S+)"),
        "program_version": re.compile(r"\bversion:\s*([^\s]+)"),
        "version_number": re.compile(r"\bversion2:\s*([^\s]+)"),
        "account_type": re.compile(r"\baccountType:\s*(\d+)"),
        "os": re.compile(r"\bos:\s*(\d+)"),
        "last_login_sdk_uid": re.compile(r"\blastloginsdkuid:\s*([^\s]+)"),
        "app_version": re.compile(r"buildGUID:\s*[^\s]+\s+version:\s*([^\s]+)"),
        "build_guid": re.compile(r"buildGUID:\s*([^\s]+)"),
    }
    hot_update_request_line = None
    hot_update_response_line = None
    region_line = None

    for raw_line in player_log.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "SyncGMConfig, getClientVersionAddress:" in raw_line:
            hot_update_request_line = raw_line
        elif "SyncGMConfig response, b: True str:" in raw_line:
            hot_update_response_line = raw_line
        elif "RequestRegionInfo response, b: True str:" in raw_line:
            region_line = raw_line
        elif "baseUrl:" in raw_line:
            state["base_url"] = raw_line.split("baseUrl:", 1)[1].strip()
        elif "buildGUID:" in raw_line:
            for key in ("app_version", "build_guid"):
                match = patterns[key].search(raw_line)
                if match:
                    state[key] = match.group(1)
        elif "SSSDKSystem Initialized DistinctId:" in raw_line:
            state["analysis_distinct_id"] = raw_line.split("DistinctId:", 1)[1].strip()
        elif '"#event_name":"ta_app_start"' in raw_line:
            json_start = raw_line.find("{")
            if json_start >= 0:
                try:
                    payload = json.loads(raw_line[json_start:])
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    props = payload.get("properties", {})
                    if isinstance(props, dict):
                        for src_key, dst_key in (
                            ("device_model", "device_model"),
                            ("os_name", "os_name"),
                            ("os_ver", "os_ver"),
                            ("network", "network"),
                            ("ip", "ip"),
                        ):
                            value = props.get(src_key)
                            if value not in (None, ""):
                                state[dst_key] = str(value)
                    for src_key, dst_key in (
                        ("#distinct_id", "analysis_distinct_id"),
                        ("#uuid", "device_uuid"),
                    ):
                        value = payload.get(src_key)
                        if value not in (None, ""):
                            state[dst_key] = str(value)

    if hot_update_request_line:
        for key, pattern in patterns.items():
            match = pattern.search(hot_update_request_line)
            if match:
                state[key] = match.group(1)

    if hot_update_response_line:
        json_start = hot_update_response_line.find("{")
        if json_start >= 0:
            try:
                payload = json.loads(hot_update_response_line[json_start:])
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                for key in ("currentVersion", "server_id", "ssServerUrl", "ipAddress"):
                    value = payload.get(key)
                    if value not in (None, ""):
                        state[key] = str(value)
                current_version = payload.get("currentVersion")
                if isinstance(current_version, str) and "_" in current_version:
                    program_version, patch_version = current_version.split("_", 1)
                    state["program_version_from_current"] = program_version
                    state["patch_version"] = patch_version

    if region_line:
        json_start = region_line.find("{")
        if json_start >= 0:
            try:
                payload = json.loads(region_line[json_start:])
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                for key in ("gate_tcp_ip", "gate_tcp_port", "client_log_tcp_ip", "client_log_tcp_port", "currentVersion"):
                    value = payload.get(key)
                    if value not in (None, ""):
                        state[key] = str(value)
                current_version = payload.get("currentVersion")
                if isinstance(current_version, str) and "_" in current_version:
                    program_version, patch_version = current_version.split("_", 1)
                    state["program_version_from_current"] = program_version
                    state["patch_version"] = patch_version

    return state


def load_env_file(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def apply_pcsdkui_fallbacks(repo_root: Path, env: dict) -> dict:
    auth_state = load_pcsdkui_auth_state(repo_root)
    if not auth_state:
        return env

    if not env.get("OF_SDK_UID"):
        candidate = auth_state.get("env_candidates", {}).get("OF_SDK_UID", "")
        if candidate:
            env["OF_SDK_UID"] = candidate

    if not env.get("OF_LOGIN_TOKEN"):
        candidate = auth_state.get("env_candidates", {}).get("OF_LOGIN_TOKEN", "")
        if candidate:
            env["OF_LOGIN_TOKEN"] = candidate

    return env


def apply_local_log_fallbacks(env: dict) -> dict:
    qgpc_state = load_qgpc_login_state()
    if qgpc_state:
        if qgpc_state.get("uid"):
            env["OF_SDK_UID"] = qgpc_state["uid"]
        if qgpc_state.get("user_token"):
            env["OF_LOGIN_TOKEN"] = qgpc_state["user_token"]
        if qgpc_state.get("username") and not env.get("OF_USERNAME"):
            env["OF_USERNAME"] = qgpc_state["username"]
        if qgpc_state.get("auth_token") and not env.get("OF_AUTH_TOKEN"):
            env["OF_AUTH_TOKEN"] = qgpc_state["auth_token"]

    player_state = load_player_log_state()
    if player_state:
        def should_override(env_key: str) -> bool:
            value = env.get(env_key, "")
            if not value:
                return True
            if env_key == "OF_HOST" and value in {"127.0.0.1", "localhost"}:
                return True
            if env_key == "OF_PORT" and value in {"11033", "11003"}:
                return True
            if env_key == "OF_ACCOUNT_TYPE" and value == "0":
                return True
            if env_key == "OF_DEVICE_MODEL" and value == "Windows PC":
                return True
            if env_key == "OF_OS_NAME" and value == "Windows":
                return True
            if env_key == "OF_OS_VER" and value == "10":
                return True
            if env_key == "OF_NETWORK" and value == "wifi":
                return True
            if env_key == "OF_IP" and value == "127.0.0.1":
                return True
            if env_key == "OF_DEVICE_UUID" and value == "codex-overfield-probe":
                return True
            return False

        mappings = {
            "OF_HOST": "gate_tcp_ip",
            "OF_PORT": "gate_tcp_port",
            "OF_RESOURCE_VERSION": "patch_version",
            "OF_CLIENT_VERSION": "program_version",
            "OF_VERSION_NUMBER": "build_guid",
            "OF_ACCOUNT_TYPE": "account_type",
            "OF_OS": "os",
            "OF_DEVICE_MODEL": "device_model",
            "OF_OS_NAME": "os_name",
            "OF_OS_VER": "os_ver",
            "OF_NETWORK": "network",
            "OF_IP": "ip",
            "OF_DEVICE_UUID": "device_uuid",
            "OF_ANALYSIS_DISTINCT_ID": "analysis_distinct_id",
            "OF_DISPATCH_URL": "dispatch_url",
            "OF_BASE_URL": "base_url",
            "OF_SERVER_ID": "server_id",
            "OF_SS_SERVER_URL": "ssServerUrl",
            "OF_DISPATCH_SERVER_IP": "ipAddress",
        }
        for env_key, state_key in mappings.items():
            if not should_override(env_key):
                continue
            value = player_state.get(state_key, "")
            if value:
                env[env_key] = value

        if not qgpc_state.get("uid") and should_override("OF_SDK_UID"):
            value = player_state.get("last_login_sdk_uid", "")
            if value:
                env["OF_SDK_UID"] = value

    return env


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def parse_int(env: dict, key: str, default: int) -> int:
    value = env.get(key, "")
    if value == "":
        return default
    return int(value)


def parse_csv_ints(env: dict, key: str) -> list[int]:
    raw = env.get(key, "")
    if not raw:
        return []
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def message_to_json_dict(message) -> dict:
    return MessageToDict(
        message,
        preserving_proto_field_name=True,
        use_integers_for_enums=True,
        always_print_fields_with_no_presence=True,
    )


class GameProbeClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.sock = socket.create_connection((host, port), timeout=10)
        self.seq_id = 1
        self.packet_id = 1

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def _send_packet(self, msg_id: int, body: bytes) -> int:
        packet_id = self.packet_id
        self.packet_id += 1

        head = PacketHead()
        head.packet_id = packet_id
        head.msg_id = msg_id
        head.flag = 0
        head.body_len = len(body)
        if msg_id in NOSEQ_COMMANDS:
            head.seq_id = 0
        else:
            head.seq_id = self.seq_id
            self.seq_id += 1

        head_bytes = head.SerializeToString()
        frame = HEADER_STRUCT.pack(len(head_bytes)) + head_bytes + body
        self.sock.sendall(frame)
        return packet_id

    def send_proto(self, msg_id: int, proto_message) -> int:
        return self._send_packet(msg_id, proto_message.SerializeToString())

    def recv_frame(self) -> dict:
        header_len = HEADER_STRUCT.unpack(self._recv_exact(2))[0]
        header_bytes = self._recv_exact(header_len)
        head = PacketHead()
        head.ParseFromString(header_bytes)
        body = self._recv_exact(head.body_len)
        if head.flag == 1:
            body = snappy.uncompress(body)

        parsed = None
        response_type = KNOWN_RESPONSE_TYPES.get(head.msg_id)
        if response_type is not None:
            parsed = response_type()
            parsed.ParseFromString(body)

        return {
            "msg_id": head.msg_id,
            "msg_name": ID_TO_NAME.get(head.msg_id, f"UNKNOWN_{head.msg_id}"),
            "packet_id": head.packet_id,
            "seq_id": head.seq_id,
            "flag": head.flag,
            "body_len": len(body),
            "parsed": message_to_json_dict(parsed) if parsed is not None else None,
            "raw_b64": None if parsed is not None else base64.b64encode(body).decode("ascii"),
        }

    def recv_until_packet_id(self, packet_id: int, timeout_seconds: float) -> tuple[dict | None, list[dict]]:
        frames = []
        deadline = time.time() + timeout_seconds
        self.sock.settimeout(timeout_seconds)
        while time.time() < deadline:
            frame = self.recv_frame()
            frames.append(frame)
            if frame["packet_id"] == packet_id:
                return frame, frames
        return None, frames

    def drain_for(self, duration_seconds: float) -> list[dict]:
        frames = []
        deadline = time.time() + duration_seconds
        self.sock.settimeout(max(duration_seconds, 0.5))
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            self.sock.settimeout(max(min(remaining, 0.5), 0.1))
            try:
                frames.append(self.recv_frame())
            except socket.timeout:
                break
        return frames

    def _recv_exact(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            part = self.sock.recv(size - len(chunks))
            if not part:
                raise ConnectionError("Socket closed while reading frame")
            chunks.extend(part)
        return bytes(chunks)


def build_verify_login_req(env: dict) -> VerifyLoginTokenReq:
    req = VerifyLoginTokenReq()
    req.account_type = parse_int(env, "OF_ACCOUNT_TYPE", 0)
    req.sdk_uid = env.get("OF_SDK_UID", "")
    req.login_token = env.get("OF_LOGIN_TOKEN", "")
    req.channel_code = env.get("OF_CHANNEL_CODE", "")
    req.os = parse_int(env, "OF_OS", AccountOSType.Windows)
    req.device_uuid = env.get("OF_DEVICE_UUID", "codex-overfield-probe")
    req.language = parse_int(env, "OF_LANGUAGE", LanguageType.LanguageType_KO)
    return req


def build_player_login_req(env: dict) -> PlayerLoginReq:
    req = PlayerLoginReq()
    req.lang = "ko"
    req.client_version = env.get("OF_CLIENT_VERSION", "")
    req.resource_version = env.get("OF_RESOURCE_VERSION", "")
    req.device_uuid = env.get("OF_DEVICE_UUID", "codex-overfield-probe")
    req.device_model = env.get("OF_DEVICE_MODEL", "Windows PC")
    req.version_number = env.get("OF_VERSION_NUMBER", "")
    req.analysis_distinct_id = env.get("OF_ANALYSIS_DISTINCT_ID", "")
    req.network = env.get("OF_NETWORK", "wifi")
    req.ip = env.get("OF_IP", "127.0.0.1")
    req.ipv6 = env.get("OF_IPV6", "")
    req.os_name = env.get("OF_OS_NAME", "Windows")
    req.os_ver = env.get("OF_OS_VER", "10")
    req.language = parse_int(env, "OF_LANGUAGE", LanguageType.LanguageType_KO)
    return req


def build_npc_talk_req(npc_id: int) -> NpcTalkReq:
    req = NpcTalkReq()
    req.id = npc_id
    req.talk_type = NpcTalkReq.NpcTalkType.NpcTalkType_Npc
    return req


def main() -> None:
    repo_root = REPO_ROOT
    env_path = repo_root / ".overfield-live.env"
    if not env_path.exists():
        raise FileNotFoundError(
            f"Missing local env file: {env_path}. Copy .overfield-live.env.example and fill it locally."
        )

    env = load_env_file(env_path)
    env = apply_pcsdkui_fallbacks(repo_root, env)
    env = apply_local_log_fallbacks(env)
    host = env.get("OF_HOST", "")
    port = parse_int(env, "OF_PORT", 11033)
    if not host:
        raise ValueError("OF_HOST is required in .overfield-live.env")

    output_path = repo_root / env.get("OF_PROBE_OUTPUT", "AutoShopGather/output/live_daily_job_probe.json")
    npc_talk_ids = parse_csv_ints(env, "OF_NPC_TALK_IDS")

    client = GameProbeClient(host, port)
    all_frames = []
    steps = []

    try:
        verify_req = build_verify_login_req(env)
        verify_packet_id = client.send_proto(MsgId.VerifyLoginTokenReq, verify_req)
        verify_rsp, frames = client.recv_until_packet_id(verify_packet_id, 5.0)
        all_frames.extend(frames)
        steps.append({"step": "verify_login_token", "packet_id": verify_packet_id, "response": verify_rsp})

        login_req = build_player_login_req(env)
        login_packet_id = client.send_proto(MsgId.PlayerLoginReq, login_req)
        login_rsp, frames = client.recv_until_packet_id(login_packet_id, 5.0)
        all_frames.extend(frames)
        steps.append({"step": "player_login", "packet_id": login_packet_id, "response": login_rsp})

        main_packet_id = client.send_proto(MsgId.PlayerMainDataReq, PlayerMainDataReq())
        main_rsp, frames = client.recv_until_packet_id(main_packet_id, 5.0)
        all_frames.extend(frames)
        all_frames.extend(client.drain_for(1.0))
        steps.append({"step": "player_main_data", "packet_id": main_packet_id, "response": main_rsp})

        for npc_id in npc_talk_ids:
            talk_packet_id = client.send_proto(MsgId.NpcTalkReq, build_npc_talk_req(npc_id))
            talk_rsp, frames = client.recv_until_packet_id(talk_packet_id, 3.0)
            all_frames.extend(frames)
            all_frames.extend(client.drain_for(0.75))
            steps.append(
                {
                    "step": "npc_talk",
                    "npc_id": npc_id,
                    "packet_id": talk_packet_id,
                    "response": talk_rsp,
                }
            )
    finally:
        client.close()

    payload = {
        "captured_at": int(time.time()),
        "host": host,
        "port": port,
        "npc_talk_ids": npc_talk_ids,
        "steps": steps,
        "frames": all_frames,
    }
    ensure_parent(output_path)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote probe dump to {output_path}")


if __name__ == "__main__":
    main()
