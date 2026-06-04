"""Tests for domain/biomech_analyzer.py."""
from run_page.analysis.domain import (
    CategorizedLaps,
    compute_biomech_delta,
)
from run_page.analysis.tests.conftest import make_lap


def _main(cad_first, cad_last, vosc_first, vosc_last, gct_first, gct_last):
    return CategorizedLaps(
        warmup=(), main=(
            make_lap(index=0, distance_m=1500, elapsed_s=300, cadence=cad_first, vosc=vosc_first, gct=gct_first),
            make_lap(index=1, distance_m=1500, elapsed_s=300, cadence=cad_last, vosc=vosc_last, gct=gct_last),
        ), recovery=(), cooldown=(), other=()
    )


def test_no_fatigue_when_cadence_stable():
    cat = _main(180, 180, 65.0, 65.0, 200, 200)
    bio = compute_biomech_delta(cat.main, cat.all())
    assert bio.fatigue_grade == "none"


def test_mild_fatigue_when_cadence_drops_4():
    cat = _main(180, 176, 65.0, 65.0, 200, 200)
    bio = compute_biomech_delta(cat.main, cat.all())
    assert bio.fatigue_grade in ("mild", "none")  # threshold: > 3 → mild


def test_notable_fatigue_when_cadence_drops_significantly():
    cat = _main(180, 170, 60.0, 65.0, 200, 215)  # cad -10, vosc +5, gct +15
    bio = compute_biomech_delta(cat.main, cat.all())
    assert bio.fatigue_grade == "notable"


def test_uses_main_laps_when_available():
    cat = CategorizedLaps(
        warmup=(make_lap(index=0, distance_m=2000, elapsed_s=720, cadence=170, vosc=70, gct=210),),
        main=(
            make_lap(index=1, distance_m=1500, elapsed_s=300, cadence=180, vosc=60, gct=190),
            make_lap(index=2, distance_m=1500, elapsed_s=300, cadence=170, vosc=70, gct=215),
        ),
        recovery=(), cooldown=(), other=()
    )
    bio = compute_biomech_delta(cat.main, cat.all())
    # Compares index 1 vs index 2 (main), not warmup
    assert bio.cadence_spm_first == 180
    assert bio.cadence_spm_last == 170


def test_falls_back_to_all_laps_when_main_too_short():
    cat = CategorizedLaps(
        warmup=(), main=(
            make_lap(index=0, distance_m=1500, elapsed_s=300, cadence=180, vosc=60, gct=190),
        ),
        recovery=(), cooldown=(), other=(
            make_lap(index=1, distance_m=2000, elapsed_s=720, cadence=170, vosc=70, gct=210),
        )
    )
    bio = compute_biomech_delta(cat.main, cat.all())
    # Single main → falls back to all laps → first and last from all
    assert bio.cadence_spm_first == 180
    assert bio.cadence_spm_last == 170


def test_empty_returns_neutral():
    cat = CategorizedLaps(warmup=(), main=(), recovery=(), cooldown=(), other=())
    bio = compute_biomech_delta(cat.main, cat.all())
    assert bio.fatigue_grade == "none"
    assert bio.cadence_spm_first is None


def test_missing_cadence_does_not_crash():
    cat = CategorizedLaps(
        warmup=(), main=(
            make_lap(index=0, distance_m=1500, elapsed_s=300, vosc=60, gct=190),
            make_lap(index=1, distance_m=1500, elapsed_s=300, vosc=70, gct=215),
        ), recovery=(), cooldown=(), other=()
    )
    bio = compute_biomech_delta(cat.main, cat.all())
    # No cadence data → no cad drop, only vosc + gct
    assert bio.cadence_spm_first is None
    assert bio.fatigue_grade in ("mild", "notable")
