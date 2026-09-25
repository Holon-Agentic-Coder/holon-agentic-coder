import pathlib
import re
import subprocess

SCRIPT_PATH = pathlib.Path(__file__).parent.parent / "build_all_images.sh"


def test_get_timestamp_with_epochrealtime():
    # Test the default branch where EPOCHREALTIME is active (simulated fallback for Bash < 5.0)
    cmd = [
        "bash",
        "-c",
        f'export EPOCHREALTIME="${{EPOCHREALTIME:-1700000000.123456}}" && source {SCRIPT_PATH} && ts="" && get_timestamp ts && echo "$ts"',
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    ts = result.stdout.strip()
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$", ts)


def test_get_timestamp_without_epochrealtime():
    # Test the fallback branch where EPOCHREALTIME is unset
    cmd = ["bash", "-c", f'unset EPOCHREALTIME && source {SCRIPT_PATH} && ts="" && get_timestamp ts && echo "$ts"']
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    ts = result.stdout.strip()
    # Fallback uses seconds precision format
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", ts)


def test_print_log_with_timestamps():
    # Test that print_log_with_timestamps correctly prepends timestamps
    cmd = [
        "bash",
        "-c",
        f'export EPOCHREALTIME="${{EPOCHREALTIME:-1700000000.123456}}" && source {SCRIPT_PATH} && echo "test log line" | print_log_with_timestamps',
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    output = result.stdout.strip()
    assert re.match(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\] test log line$", output)
