import json
import re
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.entrypoint.plan_id_unique import PLAN_ID_CLAIM_ROOT, mint_plan_id
from sandbox_executor.git import GitRepository
from sandbox_executor.ledger.fragments import pending_dir_for
from sandbox_executor.naming import GENERATION_TICKER, StageIdCollisionError, parse_stage_id
from sandbox_executor.plan_paths import PLAN_FILE_PATTERN, plan_file_rel
from sandbox_executor.ref_names import RefTooLongError, ensure_stage_segment_fits

SEQ = 1795000000
AGENT_MODEL = ("claude-agent", "claude-opus-5-5")
BASE = f"P-{SEQ}-CLA-CO5.5"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def _repo(tmp_path: Path) -> GitRepository:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
    return GitRepository(str(tmp_path))


def _mint(repo: GitRepository, tmp_path: Path, prefix: str) -> str:
    return mint_plan_id(repo, str(tmp_path), prefix, SEQ, AGENT_MODEL)


def _claimed(tmp_path: Path, plan_id: str) -> bool:
    res = subprocess.run(["git", "show-ref", "--verify", "--quiet", PLAN_ID_CLAIM_ROOT + plan_id], cwd=tmp_path)
    return res.returncode == 0


def _assert_parser_compatible(prefix: str, plan_id: str) -> None:
    parsed = parse_stage_id(plan_id)
    assert parsed is not None
    assert (parsed.generation, parsed.kind, parsed.epoch) == (GENERATION_TICKER, "P", SEQ)
    assert (parsed.ticker, parsed.model_code) == ("CLA", "CO5.5")
    assert parsed.variant is None or 2 <= parsed.variant <= 9
    assert re.fullmatch(PLAN_FILE_PATTERN, plan_file_rel(prefix, plan_id))
    ensure_stage_segment_fits(prefix, plan_id, extra=len(plan_id) + 1)


def test_same_second_mints_for_different_intent_slugs_return_different_plan_ids(tmp_path):
    repo = _repo(tmp_path)

    first, second = _mint(repo, tmp_path, "I-1-alpha"), _mint(repo, tmp_path, "I-2-beta")

    assert (first, second) == (BASE, f"{BASE}-2")
    assert _claimed(tmp_path, first) and _claimed(tmp_path, second)
    _assert_parser_compatible("I-1-alpha", first)
    _assert_parser_compatible("I-2-beta", second)


def test_a_plan_id_in_another_flows_ledger_row_is_never_minted_again(tmp_path):
    repo = _repo(tmp_path)
    ledger = tmp_path / "holon-knowledge" / "ledger" / "plans.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({"plan_id": BASE, "intent_branch": "I-9-other/_"}) + "\n")

    assert _mint(repo, tmp_path, "I-1-alpha") == f"{BASE}-2"


def test_a_plan_id_in_another_flows_pending_fragment_is_never_minted_again(tmp_path):
    repo = _repo(tmp_path)
    pending = Path(pending_dir_for(str(tmp_path / "holon-knowledge" / "ledger" / "plans.jsonl")))
    pending.mkdir(parents=True)
    (pending / "I-9-other.jsonl").write_text(json.dumps({"plan_id": BASE, "intent_branch": "I-9-other/_"}) + "\n")

    assert _mint(repo, tmp_path, "I-1-alpha") == f"{BASE}-2"


@pytest.mark.parametrize(
    "ref",
    [
        f"refs/remotes/origin/I-9-other/{BASE}/_",
        f"refs/remotes/origin/I-9-other--1-child/{BASE}/_",
        f"refs/heads/I-9-other/{BASE}/E-{SEQ}-CLA-CO5.5/_",
    ],
)
def test_a_plan_id_on_another_flows_branch_is_never_minted_again(tmp_path, ref):
    repo = _repo(tmp_path)
    _git(tmp_path, "update-ref", ref, "HEAD")

    assert _mint(repo, tmp_path, "I-1-alpha") == f"{BASE}-2"


def test_a_plan_file_of_another_flow_is_never_minted_again(tmp_path):
    repo = _repo(tmp_path)
    plan_file = tmp_path / plan_file_rel("I-9-other", BASE)
    plan_file.parent.mkdir(parents=True)
    plan_file.write_text("# plan\n")
    (plan_file.parent / f"{BASE}-2_calibration.md").write_text("# calibration\n")

    assert _mint(repo, tmp_path, "I-1-alpha") == f"{BASE}-2"


def test_a_lost_claim_re_mints_the_next_same_second_variant(tmp_path):
    repo = _repo(tmp_path)
    _git(tmp_path, "update-ref", PLAN_ID_CLAIM_ROOT + BASE, "HEAD")

    assert _mint(repo, tmp_path, "I-1-alpha") == f"{BASE}-2"
    assert _claimed(tmp_path, f"{BASE}-2")


def test_exhausted_same_second_variants_raise_a_collision(tmp_path):
    repo = _repo(tmp_path)
    for plan_id in (BASE, *(f"{BASE}-{n}" for n in range(2, 10))):
        _git(tmp_path, "update-ref", f"refs/remotes/origin/I-9-other/{plan_id}/_", "HEAD")

    with pytest.raises(StageIdCollisionError):
        _mint(repo, tmp_path, "I-1-alpha")


def test_an_over_long_intent_prefix_still_raises_ref_too_long(tmp_path):
    repo = _repo(tmp_path)

    with pytest.raises(RefTooLongError):
        _mint(repo, tmp_path, "I-1-" + "x" * 300)
