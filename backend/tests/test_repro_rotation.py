"""The release bar's rotating repro set (owner ruling, 2026-10-08).

Every release cycle builds the two ANCHORS (the regression pair in
scripts/release_cycle.json) plus two blueprints picked by ROTATION from a pool
of at least eight across verticals (backend/tests/repro_pool), so each pool
blueprint comes round every four cycles. Release = every build exports
certified + smoke A and smoke B pass, one commit.

The pool is DATA: nothing in the Factory may branch on which blueprint runs
(the hardwiring gate's ``blueprint_name`` form, tested in
tests/factory/test_hardwiring_gate.py). Selection is deterministic -- pool
order is the index's declared order, the cycle counter is git history, and
nothing is random.
"""

from __future__ import annotations

import importlib.util
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CYCLE_PATH = REPO_ROOT / "scripts" / "release_cycle.py"
POOL_MOD_PATH = REPO_ROOT / "scripts" / "repro_pool.py"
CONFIG_PATH = REPO_ROOT / "scripts" / "release_cycle.json"
POOL_INDEX = REPO_ROOT / "backend" / "tests" / "repro_pool" / "pool.json"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "post-deploy-smoke.yml"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def pool_mod():
    return _load("repro_pool", POOL_MOD_PATH)


@pytest.fixture
def cycle():
    return _load("release_cycle", CYCLE_PATH)


@pytest.fixture
def pool(pool_mod):
    return pool_mod.load_pool(POOL_INDEX)


# -- the pool ----------------------------------------------------------------


def test_the_pool_holds_eight_floor_complete_blueprints_with_unique_ids(pool):
    assert len(pool) >= 8
    ids = [bp["id"] for bp in pool]
    assert len(ids) == len(set(ids))
    for bp in pool:
        for field in ("id", "name", "vertical", "country", "currency", "build_level", "brief"):
            assert str(bp.get(field) or "").strip(), (bp.get("id"), field)
        assert len(bp["country"]) == 2 and bp["country"].isupper()
        assert len(bp["currency"]) == 3 and bp["currency"].isupper()
        assert bp["build_level"] in {"prototype", "light", "pilot", "production"}
    # Across verticals: no two blueprints share one.
    verticals = [bp["vertical"] for bp in pool]
    assert len(verticals) == len(set(verticals))


def test_a_blueprints_id_is_its_file_name_so_it_never_drifts(pool):
    for bp in pool:
        assert bp["file"] == bp["id"] + ".json"


def test_pool_order_is_the_indexs_declared_order_not_the_filesystems(pool):
    declared = json.loads(POOL_INDEX.read_text(encoding="utf-8"))["blueprints"]
    assert [bp["file"] for bp in pool] == declared
    # The declared order is not the alphabetical one, so a loader that globbed
    # the folder would visibly disagree with it.
    assert declared != sorted(declared)


def test_the_first_rotation_pair_is_fintech_then_hotel_operations(pool):
    """Owner: fintech is rotation pick #1 on the first cycle -- the money
    contract and the country/currency intake get exercised for real."""
    assert "ledger" in pool[0]["brief"] and "KYC" in pool[0]["brief"]
    assert pool[0]["currency"] != "USD"
    assert "hotel" in pool[1]["brief"]


def _write_pool(root: Path, docs, order=None):
    root.mkdir(parents=True, exist_ok=True)
    files = []
    for doc in docs:
        name = f"{doc['id']}.json"
        (root / name).write_text(json.dumps(doc), encoding="utf-8")
        files.append(name)
    index = {"schema": "repro_pool.v1", "picks_per_cycle": 2, "blueprints": order or files}
    (root / "pool.json").write_text(json.dumps(index), encoding="utf-8")
    return root / "pool.json"


def _doc(bid, **over):
    doc = {
        "schema": "repro_blueprint.v1", "id": bid, "name": f"{bid} name",
        "vertical": f"{bid}_vertical", "country": "FR", "currency": "EUR",
        "build_level": "production", "brief": f"Build me {bid}.",
    }
    doc.update(over)
    return doc


@pytest.mark.parametrize(
    "bad",
    [
        {"country": "France"},
        {"currency": "euro"},
        {"build_level": "deluxe"},
        {"brief": ""},
        {"vertical": ""},
        {"name": ""},
    ],
)
def test_a_blueprint_that_is_not_floor_complete_is_refused(pool_mod, tmp_path, bad):
    index = _write_pool(tmp_path / "p", [_doc("repro_a"), _doc("repro_b", **bad)])
    with pytest.raises(pool_mod.PoolError):
        pool_mod.load_pool(index)


def test_a_file_in_the_folder_but_not_in_the_index_is_refused(pool_mod, tmp_path):
    """Every pool file is in the rotation: an unlisted one would never be built."""
    index = _write_pool(tmp_path / "p", [_doc("repro_a"), _doc("repro_b")], order=["repro_a.json"])
    with pytest.raises(pool_mod.PoolError):
        pool_mod.load_pool(index)


def test_an_index_entry_with_no_file_or_a_duplicate_is_refused(pool_mod, tmp_path):
    index = _write_pool(tmp_path / "p", [_doc("repro_a")], order=["repro_a.json", "repro_gone.json"])
    with pytest.raises(pool_mod.PoolError):
        pool_mod.load_pool(index)
    index = _write_pool(tmp_path / "q", [_doc("repro_a")], order=["repro_a.json", "repro_a.json"])
    with pytest.raises(pool_mod.PoolError):
        pool_mod.load_pool(index)


def test_an_id_that_differs_from_its_file_name_is_refused(pool_mod, tmp_path):
    root = tmp_path / "p"
    _write_pool(root, [_doc("repro_a")])
    (root / "repro_a.json").write_text(json.dumps(_doc("repro_other")), encoding="utf-8")
    with pytest.raises(pool_mod.PoolError):
        pool_mod.load_pool(root / "pool.json")


# -- the selector ------------------------------------------------------------


def test_cycle_zero_picks_fintech_and_hotel_operations(cycle, pool):
    picks = cycle.select_rotation(pool, k=0, picks=2)
    assert [bp["id"] for bp in picks] == [pool[0]["id"], pool[1]["id"]]


def test_the_selector_is_deterministic_and_walks_the_pool_in_order(cycle, pool):
    n = len(pool)
    for k in range(3 * n):
        first = [bp["id"] for bp in cycle.select_rotation(pool, k=k, picks=2)]
        again = [bp["id"] for bp in cycle.select_rotation(pool, k=k, picks=2)]
        assert first == again
        assert first == [pool[(2 * k) % n]["id"], pool[(2 * k + 1) % n]["id"]]
        assert len(set(first)) == 2, "one cycle never builds a blueprint twice"


def test_every_blueprint_comes_round_within_four_cycles(cycle, pool):
    window = math.ceil(len(pool) / 2)
    assert window == 4
    for start in range(len(pool)):
        seen = set()
        for k in range(start, start + window):
            seen |= {bp["id"] for bp in cycle.select_rotation(pool, k=k, picks=2)}
        assert seen == {bp["id"] for bp in pool}, start


def test_the_selector_never_reads_a_clock_or_a_random_source():
    source = CYCLE_PATH.read_text(encoding="utf-8") + POOL_MOD_PATH.read_text(encoding="utf-8")
    assert "import random" not in source and "from random" not in source
    assert "secrets.choice" not in source and "shuffle(" not in source


def test_a_negative_or_missing_counter_is_refused(cycle, pool):
    with pytest.raises(cycle.CycleError):
        cycle.select_rotation(pool, k=-1, picks=2)
    with pytest.raises(cycle.CycleError):
        cycle.select_rotation(pool, k=0, picks=len(pool) + 1)


# -- the cycle counter: git history, nothing stored ---------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def _commit(repo: Path, rel: str, text: str, msg: str) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_the_counter_is_the_number_of_master_commits_since_the_pool_landed(cycle, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    before = _commit(repo, "README", "x", "before the pool")
    landed = _commit(repo, "backend/tests/repro_pool/pool.json", "{}", "the pool lands")
    one = _commit(repo, "a.txt", "1", "next merge")
    two = _commit(repo, "b.txt", "2", "the merge after")
    rel = "backend/tests/repro_pool/pool.json"
    assert cycle.rotation_index(landed, repo_root=repo, index_rel=rel) == 0
    assert cycle.rotation_index(one, repo_root=repo, index_rel=rel) == 1
    assert cycle.rotation_index(two, repo_root=repo, index_rel=rel) == 2
    # A re-run on the same commit picks the same pair.
    assert cycle.rotation_index(two, repo_root=repo, index_rel=rel) == 2
    # Editing the index later does not reset the rotation.
    three = _commit(repo, rel, '{"edited": true}', "edit the pool")
    assert cycle.rotation_index(three, repo_root=repo, index_rel=rel) == 3
    # A commit from before the pool existed has no rotation: fail closed.
    with pytest.raises(cycle.CycleError):
        cycle.rotation_index(before, repo_root=repo, index_rel=rel)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_a_shallow_checkout_fails_closed_instead_of_guessing(cycle, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q")
    _commit(src, "backend/tests/repro_pool/pool.json", "{}", "the pool lands")
    _commit(src, "a.txt", "1", "next")
    head = _commit(src, "b.txt", "2", "after")
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", src.as_uri(), str(shallow)],
        check=True, capture_output=True,
    )
    with pytest.raises(cycle.CycleError):
        cycle.rotation_index(head, repo_root=shallow, index_rel="backend/tests/repro_pool/pool.json")


# -- planning one cycle -------------------------------------------------------


def test_a_cycle_runs_the_anchors_then_the_rotation_picks(cycle, pool):
    config = cycle.load_config(CONFIG_PATH)
    runs = cycle.plan_runs(config, pool, k=0, picks=2)
    anchors = [r for r in runs if r["role"] == "anchor"]
    picks = [r for r in runs if r["role"] == "rotation"]
    assert [r["name"] for r in anchors] == [a["name"] for a in config["anchors"]]
    assert [r["name"] for r in picks] == [pool[0]["id"], pool[1]["id"]]
    assert [r["role"] for r in runs] == ["anchor"] * len(anchors) + ["rotation"] * 2
    names = [r["name"] for r in runs]
    assert len(names) == len(set(names))
    # A pick carries its blueprint's typed intake; an anchor declares none.
    assert picks[0]["intake"] == {
        "vertical": pool[0]["vertical"],
        "country": pool[0]["country"],
        "currency": pool[0]["currency"],
    }
    assert picks[0]["level"] == pool[0]["build_level"]
    assert all(not r["intake"] for r in anchors)


def test_the_anchors_are_still_the_regression_pair(cycle):
    config = cycle.load_config(CONFIG_PATH)
    assert len(config["anchors"]) == 2
    assert config["rotation"]["pool"] == "backend/tests/repro_pool/pool.json"
    rotation = cycle.load_rotation(config)
    # How many picks a cycle takes has ONE source: the pool index.
    assert rotation["picks"] == 2
    assert "picks" not in config["rotation"]
    assert [bp["id"] for bp in rotation["pool"]][:2] == [
        "repro_fintech_payments_ledger", "repro_hotel_operations",
    ]


class _IntakeSmoke:
    """Records every Floor chat turn the cycle sends."""

    TRANSIENT = {502, 503, 504}
    BuildDeadline = _load(
        "post_deploy_smoke_for_intake", REPO_ROOT / "scripts" / "post_deploy_smoke.py"
    ).BuildDeadline

    def __init__(self):
        self.turns = []

    def req(self, method, path, body=None, token=None, raw=False, **kw):
        if path == "/v1/sessions/":
            return 200, {"session_id": "sess_x"}
        if path.endswith("/product/package"):
            return 409, b"{}"
        return 200, {"build": {"state": "failed"}}

    def chat_until_drafted(self, sid, tok, brief):
        self.turns.append(("draft", None, {}))
        return "blueprint", 1

    def chat(self, sid, tok, msg, action=None, value=None, fields=None, **kw):
        self.turns.append((action, value, dict(fields or {})))
        return "ok"


def test_a_pick_declares_its_country_currency_and_vertical_on_the_floor(cycle):
    smoke = _IntakeSmoke()
    intake = {"vertical": "v_x", "country": "AE", "currency": "AED"}
    cycle.drive_build(smoke, "tok", "brief", level="production", wait_s=0, poll_s=0, intake=intake)
    by_action = {a: f for a, _v, f in smoke.turns}
    # Typed on the intake line with the level, and stated again on Approve so a
    # model proposal confirmed in between can never replace what was typed.
    assert by_action["set_build_level"] == intake
    assert by_action["approve"] == intake
    assert [a for a, _v, _f in smoke.turns] == ["draft", "set_build_level", "confirm_intake", "approve"]


def test_an_anchor_declares_no_intake_exactly_as_before(cycle):
    smoke = _IntakeSmoke()
    cycle.drive_build(smoke, "tok", "brief", level="production", wait_s=0, poll_s=0)
    assert all(f == {} for _a, _v, f in smoke.turns)


def test_the_smoke_client_sends_typed_intake_fields_beside_the_action(monkeypatch):
    smoke = _load("post_deploy_smoke", REPO_ROOT / "scripts" / "post_deploy_smoke.py")
    sent = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"ok"

    def urlopen(rq, timeout=0):
        sent["body"] = json.loads(rq.data)
        return _Resp()

    monkeypatch.setattr(smoke.urllib.request, "urlopen", urlopen)
    smoke.chat("sess_x", "tok", "", action="approve", fields={"country": "AE", "currency": "AED"})
    assert sent["body"] == {"message": "", "action": "approve", "country": "AE", "currency": "AED"}


# -- accounts and slots ------------------------------------------------------


def test_the_roster_covers_every_build_plus_the_smoke(cycle):
    from app.core.trial_limits import SMOKE_PRINCIPALS, SMOKE_RESERVED_PRINCIPAL

    config = cycle.load_config(CONFIG_PATH)
    builds = len(config["anchors"]) + cycle.load_rotation(config)["picks"]
    assert len(SMOKE_PRINCIPALS) >= builds + 1
    assert SMOKE_PRINCIPALS[0] == SMOKE_RESERVED_PRINCIPAL
    plan = cycle.assign_accounts([f"r{i}" for i in range(builds)], available=len(SMOKE_PRINCIPALS))
    assert plan["smoke"] == 0 and 0 not in plan["repros"].values()


def test_a_repro_waits_out_the_queue_ahead_of_it(cycle):
    """The live box runs two user builds at once (3 slots, 1 reserved for the
    smoke); four repros run in two waves, so a second-wave repro must not be
    timed out while it is still queued."""
    assert cycle.repro_wait_s(builds=4, user_slots=2, build_wait_s=5400) == 2 * 5400
    assert cycle.repro_wait_s(builds=2, user_slots=2, build_wait_s=5400) == 5400
    assert cycle.repro_wait_s(builds=5, user_slots=2, build_wait_s=100) == 300


def test_the_config_declares_the_live_boxs_user_slots(cycle):
    config = cycle.load_config(CONFIG_PATH)
    assert int(config["user_build_slots"]) >= 1


# -- the report --------------------------------------------------------------


def _row(role, certified=True):
    return {
        "status": "exported", "role": role, "account_index": 1,
        "export": {"bytes": 10, "files": 6, "manifest": {
            "certified": certified, "build_level": {"build_level": "production"},
            "advisory_checks": [],
        }},
        "wall_s": 120.0,
    }


def _report(cycle, pick_certified=True):
    return cycle.build_report(
        smoke_a={"status": "pass"},
        repros={
            "coop": _row("anchor"), "vineyard": _row("anchor"),
            "repro_a": _row("rotation"), "repro_b": _row("rotation", pick_certified),
        },
        smoke_b={"status": "pass"},
        started_at=0.0, finished_at=4000.0, commit="abc", live_sha="abc",
        rotation={"k": 0, "picks": ["repro_a", "repro_b"], "pool_size": 8},
    )


def test_the_report_has_a_row_per_pick_with_the_same_columns(cycle):
    report = _report(cycle)
    assert report["verdict"] == "pass"
    assert report["rotation"] == {"k": 0, "picks": ["repro_a", "repro_b"], "pool_size": 8}
    for name in ("repro_a", "repro_b"):
        row = report["runs"][name]
        assert row["role"] == "rotation"
        assert set(row["export"]["manifest"]) == {"certified", "build_level", "advisory_checks"}
        assert row["wall_s"] == 120.0
    md = cycle.render_markdown(report)
    assert "| Run | Role |" in md
    assert "repro_a" in md and "repro_b" in md and "rotation" in md
    assert "k=0" in md


def test_an_uncertified_rotation_pick_fails_the_release(cycle):
    report = _report(cycle, pick_certified=False)
    assert report["verdict"] == "fail"
    assert "repro_b" in report["reason"]


# -- the workflow ------------------------------------------------------------


def _jobs():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))["jobs"]


def test_the_repro_job_checks_out_full_history_for_the_counter():
    steps = _jobs()["release-repros"]["steps"]
    checkout = [s for s in steps if s.get("uses", "").startswith("actions/checkout")]
    assert checkout and checkout[0]["with"].get("fetch-depth") == 0


def test_the_repro_job_outlasts_every_wave_of_builds(cycle):
    config = cycle.load_config(CONFIG_PATH)
    builds = len(config["anchors"]) + cycle.load_rotation(config)["picks"]
    wait_s = cycle.repro_wait_s(builds=builds, user_slots=int(config["user_build_slots"]), build_wait_s=5400)
    need_min = (wait_s + 1200) // 60
    assert int(_jobs()["release-repros"]["timeout-minutes"]) >= need_min
    assert int(_jobs()["release-repros"]["timeout-minutes"]) <= 360, "GitHub's job ceiling"
