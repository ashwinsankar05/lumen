import copy

import numpy as np
import pytest

from lumen.probe import EmissionLog, Probe
from lumen.record import (
    SCHEMA,
    build_record,
    canonical,
    load_or_create_key,
    load_record,
    public_key_hex,
    save_record,
    sign_record,
    verify_record,
)


@pytest.fixture(params=["split", "single"])
def record(request):
    probe = Probe("record-nonce", layout=request.param, duration_s=10.0)
    log = EmissionLog(probe, t0=100.0)
    for t in 100.0 + np.arange(1, 301) / 60:
        log.emit(t)
    t = 100.3 + np.arange(5) / 30
    regions = {"face": [[65, 15, 18]] * 4 + [None], "neck": [[60, 13, 16]] * 5}
    return build_record(session_id="s1", started_at="2026-10-05T10:00:00+00:00", nonce="record-nonce",
                        log=log, capture_t=t, regions=regions, pose=[(0.01, 1.0, 40.0)] * 5,
                        capture={"source": "test"}, features=None, verdict={"decision": "abstain"},
                        thresholds={})


@pytest.fixture
def key(tmp_path):
    return load_or_create_key(tmp_path / "keys" / "k.pem")


def test_record_layout(record):
    assert record["schema"] == SCHEMA
    assert record["probe"]["nonce"] == "record-nonce"
    assert record["returned"]["regions"]["face"][4] is None
    assert len(record["returned"]["pose"]) == 5


def test_signed_record_verifies_and_survives_disk(record, key, tmp_path):
    signed = sign_record(record, key)
    check = verify_record(signed, expected_public_key=public_key_hex(key))
    assert check.ok, check.problems
    path = save_record(signed, tmp_path / "r" / "record.json")
    assert verify_record(load_record(path)).ok


def test_key_is_created_once_and_reloaded(tmp_path):
    a = load_or_create_key(tmp_path / "k.pem")
    b = load_or_create_key(tmp_path / "k.pem")
    assert public_key_hex(a) == public_key_hex(b)


def test_tampered_verdict_breaks_the_signature(record, key):
    signed = sign_record(record, key)
    signed["verdict"]["decision"] = "verify"
    check = verify_record(signed)
    assert not check.signature_ok and not check.ok


def test_forged_emission_is_caught_even_when_re_signed(record, key):
    forged = copy.deepcopy(record)
    lab = forged["emitted"]["lab"]
    if record["probe"]["layout"] == "split":
        lab[50][1][2] += 0.3  # right half, b*
    else:
        lab[50][2] += 0.3
    check = verify_record(sign_record(forged, key))
    assert check.signature_ok and not check.emission_matches_nonce


def test_wrong_nonce_is_caught(record, key):
    forged = copy.deepcopy(record)
    forged["probe"]["nonce"] = "another-nonce"
    assert not verify_record(sign_record(forged, key)).emission_matches_nonce


def test_unexpected_key_is_flagged(record, key, tmp_path):
    other = load_or_create_key(tmp_path / "other.pem")
    check = verify_record(sign_record(record, key), expected_public_key=public_key_hex(other))
    assert check.signature_ok and check.trusted_key is False and not check.ok


def test_unsigned_record_fails_cleanly(record):
    check = verify_record(record)
    assert not check.ok and "signature invalid" in check.problems[0]


def test_canonical_json_refuses_nan():
    with pytest.raises(ValueError):
        canonical({"x": float("nan")})
