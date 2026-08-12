#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: Apache-2.0
"""User-facing Ram0 persistent configuration command."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from getpass import getpass
from pathlib import Path
from typing import TextIO
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ram0_config import RAM0_USER_AGENT, Ram0ConfigError, config_path, load_config, update_config, write_config


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ram0")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="store the Ram0 endpoint and API key")
    setup.add_argument("--url")
    config = commands.add_parser("config", help="inspect or update persistent configuration")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("show")
    config_commands.add_parser("test")
    set_url = config_commands.add_parser("set-url")
    set_url.add_argument("url", nargs="?")
    config_commands.add_parser("set-key")
    return parser


def _prompt(prompt: str, *, stdin: TextIO | None = None) -> str:
    if stdin is not None:
        return stdin.readline().rstrip("\r\n")
    return input(prompt)


def _secret(*, stdin: TextIO | None = None) -> str:
    if stdin is not None:
        return stdin.readline().rstrip("\r\n")
    return getpass("Ram0 API key: ")


def _test_connection(*, home: Path | None, environment: Mapping[str, str] | None, stdout: TextIO) -> None:
    config = load_config(environment, home=home, require_key=True)
    request = Request(
        f"{config.api_url}/categories",
        headers={"Authorization": f"Bearer {config.api_key}", "User-Agent": RAM0_USER_AGENT},
        method="GET",
    )
    try:
        with build_opener(_NoRedirectHandler).open(request, timeout=10) as response:
            if response.status != 200:
                raise Ram0ConfigError(f"Ram0 configuration test failed with HTTP {response.status}.")
    except HTTPError as error:
        raise Ram0ConfigError(f"Ram0 configuration test failed with HTTP {error.code}.") from None
    except (OSError, URLError):
        raise Ram0ConfigError("Ram0 configuration test could not reach the configured endpoint.") from None
    print("Ram0 configuration test: OK", file=stdout)


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    args = _parser().parse_args(argv)
    try:
        if args.command == "setup":
            url = args.url or _prompt("Ram0 API URL: ", stdin=stdin)
            key = _secret(stdin=stdin)
            path = write_config(url, key, home=home)
            print(f"Ram0 configuration saved to {path}", file=output)
        elif args.config_command == "show":
            config = load_config(environment, home=home)
            print(f"Config: {config_path(home)}", file=output)
            print(f"API URL: {config.api_url}", file=output)
            status = "configured (redacted)" if config.api_key else "missing"
            print(f"API key: {status}", file=output)
        elif args.config_command == "test":
            _test_connection(home=home, environment=environment, stdout=output)
        elif args.config_command == "set-url":
            url = args.url or _prompt("Ram0 API URL: ", stdin=stdin)
            update_config(api_url=url, home=home)
            print("Ram0 API URL updated.", file=output)
        elif args.config_command == "set-key":
            key = _secret(stdin=stdin)
            update_config(api_key=key, home=home)
            print("Ram0 API key updated.", file=output)
        return 0
    except Ram0ConfigError as error:
        print(str(error), file=errors)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(environment=os.environ))
