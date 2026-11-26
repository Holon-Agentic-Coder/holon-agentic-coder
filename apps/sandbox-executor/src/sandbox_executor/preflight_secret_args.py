from __future__ import annotations

import shlex
from collections.abc import Mapping

from sandbox_executor.agent_attribution import is_inline_json


def reference_secret_args(runner_cmd: list[str], secret_env: Mapping[str, str]) -> list[str]:
    by_value = {value: key for key, value in sorted(secret_env.items()) if value}
    binary, *args = runner_cmd
    if not any(arg in by_value for arg in args):
        return runner_cmd
    files = [
        i for i, arg in enumerate(args) if arg in by_value and i and args[i - 1] == "--settings" and is_inline_json(arg)
    ]
    prelude = "".join(f'umask 077; s{i}=$(mktemp); printf %s "${{{by_value[args[i]]}}}" >"$s{i}"; ' for i in files)
    refs = [f'"${{{by_value[a]}}}"' if a in by_value else shlex.quote(a) for a in args]
    refs = [f'"$s{i}"' if i in files else ref for i, ref in enumerate(refs)]
    return ["sh", "-c", f"{prelude}exec {shlex.quote(binary)} {' '.join(refs)}"]
