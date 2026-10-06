import pathlib
import re
import subprocess

SCRIPT_PATH = pathlib.Path(__file__).parent.parent / "build_all_images.sh"


def test_get_timestamp_with_epochrealtime():
    # Test the default branch where EPOCHREALTIME is active (simulated fallback for Bash < 5.0)
    bash_cmd = (
        'export EPOCHREALTIME="${EPOCHREALTIME:-1700000000.123456}" && '
        f'source "{SCRIPT_PATH}" && ts="" && get_timestamp ts && echo "$ts"'
    )
    cmd = ["bash", "-c", bash_cmd]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    ts = result.stdout.strip()
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$", ts)


def test_get_timestamp_without_epochrealtime():
    # Test the fallback branch where EPOCHREALTIME is unset
    bash_cmd = f'unset EPOCHREALTIME && source "{SCRIPT_PATH}" && ts="" && get_timestamp ts && echo "$ts"'
    cmd = ["bash", "-c", bash_cmd]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    ts = result.stdout.strip()
    # Fallback uses seconds precision format
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", ts)


def test_print_log_with_timestamps():
    # Test that print_log_with_timestamps correctly prepends timestamps
    bash_cmd = (
        'export EPOCHREALTIME="${EPOCHREALTIME:-1700000000.123456}" && '
        f'source "{SCRIPT_PATH}" && echo "test log line" | print_log_with_timestamps'
    )
    cmd = ["bash", "-c", bash_cmd]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    output = result.stdout.strip()
    assert re.match(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\] test log line$", output)


def test_dockerfile_installs_jq():
    # Verify that Dockerfile for base image explicitly includes jq in apt-get install
    dockerfile_path = pathlib.Path(__file__).parent.parent / "Dockerfile"
    content = dockerfile_path.read_text(encoding="utf-8")
    assert "jq" in content
    # Specifically ensure jq is in the apt-get install instruction
    assert re.search(r"apt-get install[^\n]+jq", content) is not None


def test_role_dispatcher_warns_on_missing_jq(tmp_path):
    # Verify that role_dispatcher.sh emits an actionable warning to stderr when jq is missing
    dispatcher_path = pathlib.Path(__file__).parent.parent / "entrypoint" / "role_dispatcher.sh"
    fake_bundle = tmp_path / "holon_auth.json"
    fake_bundle.write_text('{"api_key": "test-key"}', encoding="utf-8")

    # Run role_dispatcher with empty PATH or PATH without jq
    empty_bin_dir = tmp_path / "bin"
    empty_bin_dir.mkdir()
    # Create symlinks or minimal env for bash commands except jq
    # We run bash and override PATH so jq cannot be found
    script = f"""
    export PATH="{empty_bin_dir}:/bin:/usr/bin"
    # Ensure jq is masked
    rm -f "{empty_bin_dir}/jq"
    # Create a dummy PATH where jq does not exist
    MOCK_PATH="{tmp_path}/mock_bin"
    mkdir -p "$MOCK_PATH"
    for b in tr sed echo cat chmod dirname basename bash true; do
        target=$(command -v "$b" 2>/dev/null || true)
        if [ "$target" = "$b" ]; then target=$(type -P "$b" 2>/dev/null || true); fi
        if [ -n "$target" ]; then ln -s "$target" "$MOCK_PATH/$b"; fi
    done
    export PATH="$MOCK_PATH"
    export HOLON_SECRET_BUNDLE_PATH="{fake_bundle}"
    export HOLON_ROLE=""
    bash "{dispatcher_path}" true
    """
    cmd = ["bash", "-c", script]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0
    assert "WARNING" in result.stderr
    assert "jq" in result.stderr
    assert str(fake_bundle) in result.stderr


def test_role_dispatcher_nonfatal_on_malformed_secret_bundle(tmp_path):
    # Verify that role_dispatcher.sh does not fail under set -euo pipefail when bundle is malformed
    dispatcher_path = pathlib.Path(__file__).parent.parent / "entrypoint" / "role_dispatcher.sh"
    malformed_bundle = tmp_path / "malformed_auth.json"
    malformed_bundle.write_text("{this is not valid json!}", encoding="utf-8")

    cmd = [
        "bash",
        "-c",
        f'export HOLON_SECRET_BUNDLE_PATH="{malformed_bundle}" && '
        'export HOLON_ROLE="" && '
        f'bash "{dispatcher_path}" true',
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0
    assert "WARNING: failed to parse secret bundle" in result.stderr
