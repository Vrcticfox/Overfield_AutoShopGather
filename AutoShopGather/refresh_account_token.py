from __future__ import annotations

import argparse
import json
import os
from http.cookiejar import CookieJar
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

from AutoShopGather.refresh_live_env import (
    ENV_PATH,
    api_json,
    encrypt_secret,
    load_env_file,
)

DEFAULT_DISPATCH_URL = (
    "http://dsp-global-of.inutan.com:18881/dispatch/client_hot_update"
)


class OAuthFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.form_action = "/openapi/uloginDo"
        self.hidden_inputs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag.lower() == "form" and values.get("id") == "subform":
            self.form_action = values.get("action") or self.form_action
        if (
            tag.lower() == "input"
            and values.get("type", "").lower() == "hidden"
            and values.get("name")
        ):
            self.hidden_inputs[values["name"]] = values.get("value", "")


def find_property(node: object, names: tuple[str, ...]) -> str:
    if isinstance(node, dict):
        for name in names:
            value = node.get(name)
            if value not in (None, ""):
                return str(value)
        for value in node.values():
            found = find_property(value, names)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = find_property(value, names)
            if found:
                return found
    return ""


def load_settings(env_path: Path) -> dict[str, str]:
    settings = load_env_file(env_path) if env_path.exists() else {}
    for key, value in os.environ.items():
        if value != "":
            settings[key] = value
    return settings


def refresh_account_token(settings: dict[str, str]) -> dict[str, str]:
    oauth_url = settings.get("OF_ACCOUNT_LOGIN_URL", "")
    account_name = settings.get("OF_EMAIL") or settings.get("OF_USERNAME", "")
    auth_token = settings.get("OF_AUTH_TOKEN", "")
    missing = [
        key
        for key, value in (
            ("OF_ACCOUNT_LOGIN_URL", oauth_url),
            ("OF_EMAIL", account_name),
            ("OF_AUTH_TOKEN", auth_token),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing account refresh values: {', '.join(missing)}")

    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    common_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Overfield-AutoShopGather",
    }
    oauth_request = Request(oauth_url, headers=common_headers)
    with opener.open(oauth_request, timeout=20) as response:
        final_url = response.geturl()
        page = response.read().decode("utf-8", errors="replace")

    parser = OAuthFormParser()
    parser.feed(page)
    form = dict(parser.hidden_inputs)
    form.update(
        {
            "account": account_name,
            "accountFirst": account_name,
            "password": "******",
            "localAuthToken": auth_token,
        }
    )

    post_url = urljoin(final_url, parser.form_action)
    parsed_post_url = urlparse(post_url)
    post_headers = {
        **common_headers,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": oauth_url,
        "Origin": f"{parsed_post_url.scheme}://{parsed_post_url.netloc}",
        "X-Requested-With": "XMLHttpRequest",
    }
    login_request = Request(
        post_url,
        data=urlencode(form).encode("utf-8"),
        headers=post_headers,
        method="POST",
    )
    with opener.open(login_request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    success_url = find_property(payload, ("successUrl", "tourl"))
    new_auth_token = find_property(payload, ("authToken",))
    new_uid = find_property(payload, ("uid",))
    new_user_token = find_property(payload, ("userToken",))
    if success_url:
        query = parse_qs(urlparse(success_url).query)
        new_uid = new_uid or query.get("uid", [""])[0]
        new_user_token = new_user_token or query.get("userToken", [""])[0]
        new_auth_token = new_auth_token or query.get("authToken", [""])[0]

    if not new_uid or not new_user_token or not new_auth_token:
        status = find_property(payload, ("status",))
        raise RuntimeError(
            "Account refresh response was missing uid, userToken, or authToken "
            f"(status={status or 'unknown'})"
        )

    return {
        "OF_SDK_UID": new_uid,
        "OF_LOGIN_TOKEN": new_user_token,
        "OF_AUTH_TOKEN": new_auth_token,
    }


def refresh_runtime_config(
    settings: dict[str, str],
    login_values: dict[str, str],
) -> dict[str, str]:
    dispatch_url = settings.get("OF_DISPATCH_URL") or DEFAULT_DISPATCH_URL
    form = {
        "version": settings.get("OF_CLIENT_VERSION", ""),
        "version2": settings.get("OF_VERSION_NUMBER", ""),
        "accountType": settings.get("OF_ACCOUNT_TYPE", ""),
        "os": settings.get("OF_OS", "0"),
        "lastloginsdkuid": login_values["OF_SDK_UID"],
    }
    request = Request(
        dispatch_url,
        data=urlencode(form).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "Overfield-AutoShopGather",
        },
        method="POST",
    )
    with build_opener().open(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    current_version = str(payload.get("currentVersion") or "")
    if not payload.get("status") or "_" not in current_version:
        raise RuntimeError(
            "Runtime configuration refresh failed "
            f"(message={payload.get('message') or 'unknown'})"
        )

    client_version, resource_version = current_version.split("_", 1)
    return {
        "OF_CLIENT_VERSION": client_version,
        "OF_RESOURCE_VERSION": resource_version,
    }


def write_github_env(path: Path, values: dict[str, str]) -> None:
    for key, value in values.items():
        if "\n" in value or "\r" in value:
            raise RuntimeError(f"Refusing multiline GitHub environment value: {key}")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def sync_refresh_secrets(settings: dict[str, str], values: dict[str, str]) -> None:
    token = (
        settings.get("AUTH_REFRESH_PAT")
        or settings.get("GITHUB_SECRETS_PAT")
        or settings.get("GITHUB_PAT")
    )
    repository = settings.get("GITHUB_REPOSITORY", "")
    if "/" in repository:
        owner, repo = repository.split("/", 1)
    else:
        owner = settings.get("GITHUB_REPO_OWNER", "Vrcticfox")
        repo = settings.get("GITHUB_REPO_NAME", "Overfield_AutoShopGather")
    if not token:
        raise RuntimeError("AUTH_REFRESH_PAT is required to persist the rotated token")

    base = f"https://api.github.com/repos/{owner}/{repo}/actions/secrets"
    public_key = api_json("GET", f"{base}/public-key", token)
    if not public_key:
        raise RuntimeError("GitHub public key response was empty")

    for key in (
        "OF_AUTH_TOKEN",
        "OF_LOGIN_TOKEN",
        "OF_SDK_UID",
        "OF_CLIENT_VERSION",
        "OF_RESOURCE_VERSION",
    ):
        api_json(
            "PUT",
            f"{base}/{key}",
            token,
            {
                "encrypted_value": encrypt_secret(public_key["key"], values[key]),
                "key_id": public_key["key_id"],
            },
        )
    print("[ok] Persisted rotated account secrets for the next workflow run")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exchange the launcher auth token for fresh live login values."
    )
    parser.add_argument("--env", default=str(ENV_PATH), help="Optional local env path")
    parser.add_argument(
        "--github-env",
        default=os.environ.get("GITHUB_ENV", ""),
        help="GitHub Actions environment file to update",
    )
    parser.add_argument(
        "--sync-secrets",
        action="store_true",
        help="Persist rotated values as repository Actions secrets",
    )
    args = parser.parse_args()

    settings = load_settings(Path(args.env))
    previous_auth_token = settings.get("OF_AUTH_TOKEN", "")
    login_values = refresh_account_token(settings)
    settings.update(login_values)
    runtime_values = refresh_runtime_config(settings, login_values)
    values = {**login_values, **runtime_values}

    if args.github_env:
        write_github_env(Path(args.github_env), values)
        print("[ok] Exported fresh login values to GITHUB_ENV")
    if args.sync_secrets:
        sync_refresh_secrets(settings, values)

    print(
        "[ok] Account token refresh succeeded "
        f"(userToken={len(values['OF_LOGIN_TOKEN'])} chars, "
        f"authToken={len(values['OF_AUTH_TOKEN'])} chars, "
        f"rotated={values['OF_AUTH_TOKEN'] != previous_auth_token}, "
        f"clientVersion={values['OF_CLIENT_VERSION']}, "
        f"resourceVersion={values['OF_RESOURCE_VERSION']})"
    )


if __name__ == "__main__":
    main()
