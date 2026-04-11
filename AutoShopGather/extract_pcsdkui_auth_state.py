from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


AUTH_TOKEN_RE = re.compile(rb"(@\d{2,3}(?:@\d{2,3}){20,})")
EMAIL_RE = re.compile(rb"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
SNS_RE = re.compile(rb"([A-Za-z][A-Za-z0-9_+-]{2,32})")
LAST_LOGIN_RE = re.compile(rb"([A-Za-z][A-Za-z0-9_+-]{2,32})")


PREFERRED_FILES = (
    "000010.log",
    "000003.ldb",
    "000005.ldb",
    "000007.ldb",
)


def iter_candidate_files(leveldb_dir: Path) -> list[Path]:
    files = []
    for name in PREFERRED_FILES:
        path = leveldb_dir / name
        if path.exists() and path.is_file():
            files.append(path)
    if files:
        return files
    return sorted(
        [p for p in leveldb_dir.iterdir() if p.is_file() and p.suffix.lower() in {".log", ".ldb"}],
        key=lambda p: p.name,
    )


def pick_last_match(pattern, data: bytes) -> str | None:
    matches = pattern.findall(data)
    return matches[-1] if matches else None


def extract_near_key(data: bytes, key: bytes, pattern, window: int = 4096) -> str | None:
    idx = data.rfind(key)
    if idx < 0:
        return None
    chunk = data[idx : idx + window]
    match = pick_last_match(pattern, chunk)
    if match is None:
        return None
    if isinstance(match, bytes):
        return match.decode("latin-1", errors="ignore")
    return str(match)


def extract_state(leveldb_dir: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "leveldb_dir": str(leveldb_dir),
        "files_scanned": [],
        "local_auth_token": None,
        "local_auth_token_length": 0,
        "local_user_name": None,
        "local_sns_login_type": None,
        "last_login_type": None,
        "notes": [
            "localAuthToken is passed back into the launcher login form as-is.",
            "This value is a strong candidate for the launcher-side auth artifact, but it is not yet proven to be the game socket login_token.",
        ],
    }

    for path in iter_candidate_files(leveldb_dir):
        data = path.read_bytes()
        result["files_scanned"].append(path.name)

        auth_token = extract_near_key(data, b"localAuthToken", AUTH_TOKEN_RE)
        if auth_token:
            result["local_auth_token"] = auth_token
            result["local_auth_token_length"] = len(auth_token)

        email = extract_near_key(data, b"localUserName", EMAIL_RE)
        if email:
            result["local_user_name"] = email

        sns = extract_near_key(data, b"localSnsLoginType", SNS_RE)
        if sns:
            result["local_sns_login_type"] = sns

        last_login = extract_near_key(data, b"lastLoginType", LAST_LOGIN_RE)
        if last_login:
            result["last_login_type"] = last_login

    result["env_candidates"] = {
        "OF_SDK_UID": result["local_user_name"] or "",
        "OF_LOGIN_TOKEN": result["local_auth_token"] or "",
    }

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract launcher-side auth artifacts from PCSDKUI QtWebEngine local storage."
    )
    parser.add_argument(
        "--leveldb-dir",
        default="pcsdkui_storage/Local Storage/leveldb",
        help="Path to the QtWebEngine Local Storage leveldb directory.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    args = parser.parse_args()

    leveldb_dir = Path(args.leveldb_dir)
    if not leveldb_dir.exists():
        raise SystemExit(f"LevelDB directory not found: {leveldb_dir}")

    result = extract_state(leveldb_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
