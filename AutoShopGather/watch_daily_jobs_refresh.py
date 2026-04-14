import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from AutoShopGather.export_live_daily_jobs import OUTPUT_PATH, decode_live_jobs


KST = ZoneInfo("Asia/Seoul")
STATE_PATH = OUTPUT_PATH.with_name("live_daily_jobs_refresh_state.json")
ARCHIVE_DIR = OUTPUT_PATH.parent / "archive"
POLL_SECONDS = 3.0
START_WINDOW_MINUTES = 10
MAX_WATCH_MINUTES = 30


def now_kst() -> datetime:
    return datetime.now(KST)


def comparable_rows(payload: dict) -> list[dict]:
    rows = []
    for npc in payload.get("npcs", []):
        for item in npc.get("items", []):
            rows.append(
                {
                    "npc_name": npc.get("npc_name"),
                    "item_name": item.get("item_name"),
                    "nyang_coin_reward": item.get("nyang_coin_reward"),
                }
            )
    rows.sort(key=lambda row: (row["npc_name"] or "", row["item_name"] or "", row["nyang_coin_reward"] or 0))
    return rows


def signature(payload: dict) -> str:
    return json.dumps(comparable_rows(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_baseline_payload() -> dict | None:
    payload = load_json(OUTPUT_PATH)
    if payload:
        return payload
    return None


def load_state() -> dict:
    return load_json(STATE_PATH) or {}


def next_midnight(after: datetime) -> datetime:
    midnight = after.replace(hour=0, minute=0, second=0, microsecond=0)
    if after >= midnight:
        midnight = midnight + timedelta(days=1)
    return midnight


def current_watch_window_start(current: datetime) -> datetime:
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def target_refresh_date(current: datetime) -> str:
    watch_window_start = current_watch_window_start(current)
    watch_window_end = watch_window_start + timedelta(minutes=START_WINDOW_MINUTES)
    if current < watch_window_end:
        return current.date().isoformat()
    return (watch_window_start + timedelta(days=1)).date().isoformat()


def sleep_until(target: datetime) -> None:
    while True:
        remaining = (target - now_kst()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 30))


def archive_payload(payload: dict, detected_at: datetime) -> Path:
    archive_name = f"live_daily_jobs_{detected_at.strftime('%Y-%m-%d')}.json"
    archive_path = ARCHIVE_DIR / archive_name
    save_json(archive_path, payload)
    return archive_path


def main() -> None:
    state = load_state()
    started_at = now_kst()
    target_date = target_refresh_date(started_at)

    if state.get("detected_date") == target_date and OUTPUT_PATH.exists():
        print(f"[skip] {target_date} 는 이미 갱신 감지를 완료했습니다.")
        print(f"[skip] output: {OUTPUT_PATH}")
        return

    baseline_payload = load_baseline_payload()
    baseline_signature = signature(baseline_payload) if baseline_payload else None

    watch_window_start = current_watch_window_start(started_at)
    watch_window_end = watch_window_start + timedelta(minutes=START_WINDOW_MINUTES)

    if started_at < watch_window_start:
        print(f"[wait] 다음 감시 시작 시각: {watch_window_start.isoformat()}")
        sleep_until(watch_window_start)
    elif started_at >= watch_window_end:
        next_start = next_midnight(started_at)
        print(
            f"[wait] 현재 시각이 감시 창을 지났습니다. "
            f"다음 감시 시작 시각: {next_start.isoformat()}"
        )
        sleep_until(next_start)

    print(f"[start] 감시 시작: {now_kst().isoformat()} / 주기 {POLL_SECONDS:.0f}초")

    attempt = 0
    watch_started_at = now_kst()
    watch_deadline = watch_started_at + timedelta(minutes=MAX_WATCH_MINUTES)
    while True:
        if now_kst() >= watch_deadline:
            save_json(
                STATE_PATH,
                {
                    "detected_date": watch_started_at.date().isoformat(),
                    "detected_at": None,
                    "attempt": attempt,
                    "reason": "timeout_no_change",
                    "signature": baseline_signature,
                    "output_path": str(OUTPUT_PATH),
                    "watch_started_at": watch_started_at.isoformat(),
                    "watch_deadline": watch_deadline.isoformat(),
                },
            )
            print(f"[timeout] 감시 시간 내 갱신이 감지되지 않았습니다. attempts={attempt}")
            return

        attempt += 1
        detected_at = now_kst()
        payload = decode_live_jobs()
        current_signature = signature(payload)

        if baseline_signature is None:
            save_json(OUTPUT_PATH, payload)
            archive_path = archive_payload(payload, detected_at)
            save_json(
                STATE_PATH,
                {
                    "detected_date": detected_at.date().isoformat(),
                    "detected_at": detected_at.isoformat(),
                    "attempt": attempt,
                    "reason": "no_baseline",
                    "signature": current_signature,
                    "output_path": str(OUTPUT_PATH),
                    "archive_path": str(archive_path),
                },
            )
            print(f"[done] 기준 파일이 없어서 현재 값을 바로 저장했습니다. attempt={attempt}")
            print(f"[done] output: {OUTPUT_PATH}")
            print(f"[done] archive: {archive_path}")
            return

        if current_signature != baseline_signature:
            save_json(OUTPUT_PATH, payload)
            archive_path = archive_payload(payload, detected_at)
            save_json(
                STATE_PATH,
                {
                    "detected_date": detected_at.date().isoformat(),
                    "detected_at": detected_at.isoformat(),
                    "attempt": attempt,
                    "reason": "signature_changed",
                    "previous_signature": baseline_signature,
                    "signature": current_signature,
                    "output_path": str(OUTPUT_PATH),
                    "archive_path": str(archive_path),
                },
            )
            print(f"[done] 갱신 감지 성공. attempt={attempt}")
            print(f"[done] output: {OUTPUT_PATH}")
            print(f"[done] archive: {archive_path}")
            return

        print(f"[poll] attempt={attempt} 아직 동일함 {detected_at.isoformat()}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
