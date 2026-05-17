from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".overfield-live.env"
DEFAULT_GAME_INSTALL_DIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common\OverField")
DEFAULT_PLAYER_LOG = Path.home() / "AppData" / "LocalLow" / "Inutan" / "OverField" / "Player.log"

SECRET_KEYS = (
    "OF_HOST",
    "OF_PORT",
    "OF_SDK_UID",
    "OF_LOGIN_TOKEN",
    "OF_ACCOUNT_TYPE",
    "OF_CHANNEL_CODE",
    "OF_CLIENT_VERSION",
    "OF_RESOURCE_VERSION",
    "OF_VERSION_NUMBER",
    "OF_DEVICE_UUID",
    "OF_DEVICE_MODEL",
    "OF_ANALYSIS_DISTINCT_ID",
    "OF_NETWORK",
    "OF_IP",
    "OF_IPV6",
    "OF_OS_NAME",
    "OF_OS_VER",
    "OF_LANGUAGE",
    "OF_OS",
)


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


def load_qgpc_login_state(log_path: Path) -> dict:
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


def load_player_log_state(player_log: Path) -> dict:
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


def parse_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def game_install_dir(env: dict) -> Path:
    raw = env.get("OF_GAME_INSTALL_DIR") or str(DEFAULT_GAME_INSTALL_DIR)
    return Path(raw).expanduser()


def qgpc_log_path(env: dict) -> Path:
    if env.get("OF_QGPC_LOG_PATH"):
        return Path(env["OF_QGPC_LOG_PATH"]).expanduser()
    return game_install_dir(env) / "launcher_Data" / "Quicksdk" / "qgpc_log.txt"


def player_log_path(env: dict) -> Path:
    if env.get("OF_PLAYER_LOG_PATH"):
        return Path(env["OF_PLAYER_LOG_PATH"]).expanduser()
    return DEFAULT_PLAYER_LOG


def set_env_value(lines: list[str], key: str, value: str) -> tuple[list[str], bool]:
    prefix = f"{key}="
    updated = False
    changed = False
    out = []
    for line in lines:
        if line.startswith(prefix):
            old_value = line[len(prefix) :]
            out.append(f"{key}={value}")
            updated = True
            changed = old_value != value
        else:
            out.append(line)
    if not updated:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
        changed = True
    return out, changed


def update_env_file(path: Path, updates: dict[str, str]) -> list[str]:
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    changed_keys = []
    for key, value in updates.items():
        if value == "":
            continue
        lines, changed = set_env_value(lines, key, str(value))
        if changed:
            changed_keys.append(key)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed_keys


def collect_updates(env: dict) -> dict[str, str]:
    qgpc = load_qgpc_login_state(qgpc_log_path(env))
    player = load_player_log_state(player_log_path(env))

    updates: dict[str, str] = {}
    if qgpc.get("uid"):
        updates["OF_SDK_UID"] = qgpc["uid"]
    if qgpc.get("user_token"):
        updates["OF_LOGIN_TOKEN"] = qgpc["user_token"]

    mappings = {
        "OF_HOST": "gate_tcp_ip",
        "OF_PORT": "gate_tcp_port",
        "OF_RESOURCE_VERSION": "patch_version",
        "OF_CLIENT_VERSION": "program_version_from_current",
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
        value = player.get(state_key)
        if value not in (None, ""):
            updates[env_key] = str(value)

    return updates


def api_json(method: str, url: str, token: str, body: dict | None = None) -> dict | None:
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Overfield-AutoShopGather",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, method=method, headers=headers)
    try:
        with urlopen(req, timeout=20) as res:
            raw = res.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} {url} failed: {exc.code} {detail}") from exc


def ensure_pynacl() -> None:
    try:
        import nacl.public  # noqa: F401
    except ImportError:
        print("[setup] PyNaCl is missing. Installing it for GitHub secret encryption...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pynacl"])


def encrypt_secret(public_key: str, value: str) -> str:
    ensure_pynacl()
    from nacl import public

    key = public.PublicKey(base64.b64decode(public_key))
    sealed_box = public.SealedBox(key)
    encrypted = sealed_box.encrypt(value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def sync_github_secrets(env: dict) -> None:
    token = env.get("GITHUB_SECRETS_PAT") or env.get("GITHUB_PAT")
    owner = env.get("GITHUB_REPO_OWNER") or "Vrcticfox"
    repo = env.get("GITHUB_REPO_NAME") or "Overfield_AutoShopGather"
    if not token:
        print("[skip] GITHUB_SECRETS_PAT is not set; local env was updated only.")
        return

    base = f"https://api.github.com/repos/{owner}/{repo}/actions/secrets"
    public_key = api_json("GET", f"{base}/public-key", token)
    if not public_key:
        raise RuntimeError("GitHub public key response was empty.")

    synced = []
    for key in SECRET_KEYS:
        value = env.get(key, "")
        if value == "":
            continue
        payload = {
            "encrypted_value": encrypt_secret(public_key["key"], value),
            "key_id": public_key["key_id"],
        }
        api_json("PUT", f"{base}/{key}", token, payload)
        synced.append(key)

    print(f"[ok] Synced {len(synced)} GitHub secrets: {', '.join(synced)}")


def dispatch_workflow(env: dict) -> None:
    token = env.get("GITHUB_ACTIONS_PAT") or env.get("GITHUB_SECRETS_PAT") or env.get("GITHUB_PAT")
    owner = env.get("GITHUB_REPO_OWNER") or "Vrcticfox"
    repo = env.get("GITHUB_REPO_NAME") or "Overfield_AutoShopGather"
    workflow = env.get("GITHUB_WORKFLOW_FILE") or "daily-jobs-refresh.yml"
    ref = env.get("GITHUB_WORKFLOW_REF") or "main"
    mode = env.get("GITHUB_WORKFLOW_MODE") or "watch"
    if not token:
        print("[skip] No GitHub token is set for workflow dispatch.")
        return

    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches"
    api_json("POST", url, token, {"ref": ref, "inputs": {"mode": mode}})
    print(f"[ok] Dispatched {workflow} on {ref} with mode={mode}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh local live env values from OverField logs.")
    parser.add_argument("--env", default=str(ENV_PATH), help="Path to .overfield-live.env")
    parser.add_argument("--sync-secrets", action="store_true", help="Update GitHub Actions repository secrets.")
    parser.add_argument("--dispatch", action="store_true", help="Trigger the daily jobs workflow after updating.")
    args = parser.parse_args()

    env_path = Path(args.env)
    env = load_env_file(env_path)
    updates = collect_updates(env)
    if not updates:
        raise SystemExit("No fresh values were found in the configured OverField logs.")

    changed = update_env_file(env_path, updates)
    env = load_env_file(env_path)
    print(f"[ok] Updated {env_path}")
    print("[info] Changed keys:", ", ".join(changed) if changed else "none")

    should_sync = args.sync_secrets or parse_bool(env.get("GITHUB_SYNC_SECRETS"))
    should_dispatch = args.dispatch or parse_bool(env.get("GITHUB_AUTO_DISPATCH"))
    if should_sync:
        sync_github_secrets(env)
    if should_dispatch:
        dispatch_workflow(env)


if __name__ == "__main__":
    main()
