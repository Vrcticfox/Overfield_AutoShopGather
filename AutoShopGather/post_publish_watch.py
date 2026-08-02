import time

from AutoShopGather.export_live_daily_jobs import decode_live_jobs
from AutoShopGather.watch_daily_jobs_refresh import (
    OUTPUT_PATH,
    POLL_SECONDS,
    STABLE_CONFIRMATIONS,
    STATE_PATH,
    archive_payload,
    load_baseline_payload,
    load_state,
    now_kst,
    save_json,
    signature,
)


POST_PUBLISH_SECONDS = 20.0
CORRECTION_EXIT_CODE = 10


def confirm_post_publish_change() -> int:
    baseline_payload = load_baseline_payload()
    if not baseline_payload:
        print("[post-watch] output baseline is missing; skipping correction watch")
        return 0

    baseline_signature = signature(baseline_payload)
    candidate_signature = None
    candidate_streak = 0
    deadline = time.monotonic() + POST_PUBLISH_SECONDS
    attempt = 0

    print(
        f"[post-watch] checking for {POST_PUBLISH_SECONDS:.0f} seconds "
        f"with a {POLL_SECONDS:.0f}-second interval"
    )

    while time.monotonic() < deadline:
        attempt += 1
        detected_at = now_kst()
        payload = decode_live_jobs()
        current_signature = signature(payload)

        if current_signature == baseline_signature:
            candidate_signature = None
            candidate_streak = 0
            print(f"[post-watch] attempt={attempt} unchanged")
        else:
            if current_signature == candidate_signature:
                candidate_streak += 1
            else:
                candidate_signature = current_signature
                candidate_streak = 1

            print(
                f"[post-watch] attempt={attempt} "
                f"candidate={candidate_streak}/{STABLE_CONFIRMATIONS}"
            )
            if candidate_streak >= STABLE_CONFIRMATIONS:
                save_json(OUTPUT_PATH, payload)
                archive_path = archive_payload(payload, detected_at)
                state = load_state()
                state.update(
                    {
                        "post_publish_detected_at": detected_at.isoformat(),
                        "post_publish_attempt": attempt,
                        "post_publish_reason": "signature_changed_stable",
                        "post_publish_signature": current_signature,
                        "post_publish_archive_path": str(archive_path),
                    }
                )
                save_json(STATE_PATH, state)
                print("[post-watch] stable correction detected")
                return CORRECTION_EXIT_CODE

        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(POLL_SECONDS, remaining))

    print("[post-watch] no stable correction detected")
    return 0


if __name__ == "__main__":
    raise SystemExit(confirm_post_publish_change())
