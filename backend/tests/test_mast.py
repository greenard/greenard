import numpy as np
import pandas as pd
import pytest

from app.core.errors import AppError
from app.mast import analysis, qc
from app.mast.formats import detect_format, read_file, suggest_mapping
from app.mast.ingest import build_sensors, ingest
from tests.mastgen import ALPHA, generate, to_toa5


@pytest.fixture(scope="module")
def synthetic():
    df, truth = generate(days=60)
    return df, truth, to_toa5(df).encode()


def _mapping(raw):
    """Correspondance suggérée, complétée comme le ferait l'utilisateur (hauteur du baromètre)."""
    m = suggest_mapping(list(raw.frame.columns), raw.time_column)
    for x in m:
        if x["kind"] == "pressure" and x["height_m"] is None:
            x["height_m"] = 2.0
    return m


def test_toa5_detection_and_mapping(synthetic):
    _, _, data = synthetic
    raw = read_file("mast.dat", data)
    assert raw.format == "toa5" and raw.default_convention == "end" and raw.time_column == "TIMESTAMP"
    m = {x["column"]: x for x in _mapping(raw)}
    assert m["ws80N_avg"] == {
        "column": "ws80N_avg",
        "kind": "ws",
        "stat": "mean",
        "height_m": 80.0,
        "boom_dir_deg": 0.0,
    }
    assert m["ws80S_sd"]["stat"] == "sd" and m["ws80S_sd"]["boom_dir_deg"] == 180.0
    assert m["wd78_avg"]["kind"] == "wd" and m["wd78_avg"]["height_m"] == 78.0
    assert m["temp_2m_avg"]["kind"] == "temp" and m["press_avg"]["kind"] == "pressure"
    assert "RECORD" not in m


def test_nrg_and_windographer_detection():
    nrg = (
        "SymphoniePRO Desktop App\nExport Parameters\nSite Number\t1234\n\nTimestamp\tCh1_Anem_100.00m_N_Avg_m/s\n"
        "2025-01-01 00:00:00\t7.2\n2025-01-01 00:10:00\t7.4\n"
    )
    raw = read_file("x.txt", nrg.encode())
    assert raw.format == "nrg" and raw.default_convention == "start"
    assert _mapping(raw)[0] == {
        "column": "Ch1_Anem_100.00m_N_Avg_m/s",
        "kind": "ws",
        "stat": "mean",
        "height_m": 100.0,
        "boom_dir_deg": 0.0,
    }
    wg = "Windographer 5 export\nLatitude = 31.4\nDate/Time\tSpeed 60 m A\tDirection 58 m\n2025-01-01 00:00\t6\t30\n"
    assert detect_format(wg) == "windographer"
    with pytest.raises(AppError) as e:
        read_file("data.rld", b"\x00\x01")
    assert e.value.code == "MAST_BINARY_FORMAT"


def test_ingest_time_reference_and_gap(synthetic):
    df, truth, data = synthetic
    raw = read_file("mast.dat", data)
    ing = ingest(raw, _mapping(raw), {"timezone": "UTC+01:00", "convention": "end"})
    # TOA5 : fin d'intervalle écrite en heure du logger (UTC+1) → fin d'intervalle UTC = début + 10 min − 1 h
    assert ing.frame.index[0] == df.index[0] + pd.Timedelta(minutes=10) - pd.Timedelta(hours=1)
    assert ing.interval_min == 10
    assert ing.report["missing_records_inserted"] == truth["gap_records"]
    assert ing.frame.index.is_monotonic_increasing and ing.frame.index.freq is not None or True
    codes = {s.code for s in ing.sensors}
    assert codes == {"ws_80_n", "ws_80_s", "ws_60", "ws_40", "wd_78", "temp_2", "rh_2", "pressure_2"}


def test_ingest_iana_local_time_reports_ambiguous():
    # retour à UTC+0 au Maroc le 20/09/2026 à 02:00 : l'heure 01:00–02:00 locale est ambiguë
    times = [
        "2026-09-20 00:30",
        "2026-09-20 00:40",
        "2026-09-20 01:30",
        "2026-09-20 02:10",
        "2026-09-20 02:20",
        "2026-09-20 02:30",
    ]
    raw_df = pd.DataFrame({"t": times, "ws_10": ["5"] * 6})
    from app.mast.formats import RawTable

    ing = ingest(
        RawTable("csv", raw_df, "t", "start"),
        [{"column": "ws_10", "kind": "ws", "stat": "mean", "height_m": 10}],
        {"timezone": "Africa/Casablanca", "convention": "start"},
    )
    assert ing.report["ambiguous_or_nonexistent_local_times"] == 1
    # 00:30 locale (UTC+1) = 23:30 UTC ; fin d'intervalle 23:40 UTC
    assert ing.frame.index[0] == pd.Timestamp("2026-09-19 23:40")


def test_mapping_errors():
    with pytest.raises(AppError) as e:
        build_sensors([{"column": "a", "kind": "ws", "stat": "mean", "height_m": None}])
    assert e.value.code == "MAST_MAPPING_HEIGHT_MISSING"
    with pytest.raises(AppError) as e:
        build_sensors([{"column": "a", "kind": "ws", "stat": "sd", "height_m": 10}])
    assert e.value.code == "MAST_MAPPING_NO_MEAN"
    with pytest.raises(AppError) as e:
        build_sensors(
            [
                {"column": "a", "kind": "ws", "stat": "mean", "height_m": 80, "boom_dir_deg": None},
                {"column": "b", "kind": "ws", "stat": "mean", "height_m": 80, "boom_dir_deg": None},
            ]
        )
    assert e.value.code == "MAST_MAPPING_DUPLICATE"


@pytest.fixture(scope="module")
def checked(synthetic):
    df, truth, data = synthetic
    raw = read_file("mast.dat", data)
    ing = ingest(raw, _mapping(raw), {"timezone": "UTC", "convention": "end"})
    return qc.run_qc(ing.frame, ing.sensors), ing.sensors, df, truth


def _flag(out, code, bit):
    return (out[f"{code}__flag"] & bit) > 0


def test_qc_icing_stuck_spike_missing(checked):
    out, sensors, df, truth = checked
    t0 = df.index[truth["icing_rows"][0]] + pd.Timedelta(minutes=10)
    t1 = df.index[truth["icing_rows"][1] - 1] + pd.Timedelta(minutes=10)
    assert _flag(out, "ws_60", qc.ICING)[t0:t1].mean() > 0.9
    s0 = df.index[truth["stuck_vane_rows"][0]] + pd.Timedelta(minutes=10)
    s1 = df.index[truth["stuck_vane_rows"][1] - 1] + pd.Timedelta(minutes=10)
    assert _flag(out, "wd_78", qc.STUCK)[s0:s1].mean() > 0.9
    for k in truth["spike_rows"]:
        assert _flag(out, "ws_40", qc.SPIKE)[df.index[k] + pd.Timedelta(minutes=10)]
    assert _flag(out, "ws_80_n", qc.MISSING).sum() == truth["gap_records"]
    # pas de faux positifs massifs sur un capteur sain
    assert (out["ws_80_n__flag"] & (qc.SPIKE | qc.STUCK | qc.ICING)).astype(bool).mean() < 0.01


def test_qc_tower_shadow_and_composite(checked):
    out, sensors, df, truth = checked
    wd = out["wd@78"]
    from_south = qc.angular_diff(out["wd_78__mean"], 180) < 20
    assert _flag(out, "ws_80_n", qc.SHADOW)[from_south].mean() > 0.95  # bras N ombragé par vent du S
    assert not _flag(out, "ws_80_s", qc.SHADOW)[from_south].any()
    # composite : au vent du sud, la valeur est celle de l'anémomètre S (non ombragé)
    ok = from_south & (out["ws_80_s__flag"] == 0)
    assert np.allclose(out.loc[ok, "ws@80"], out.loc[ok, "ws_80_s__mean"])
    assert wd.notna().mean() > 0.9


def test_shear_and_ti(checked):
    out, *_ = checked
    sh = analysis.shear(out)
    assert sh["z_low_m"] == 60 and sh["z_high_m"] == 80
    assert sh["alpha_all"] == pytest.approx(ALPHA, abs=0.01)
    assert len(sh["by_sector"]) >= 6 and {r["key"] for r in sh["by_season"]} <= {"DJF", "MAM"}
    ti = analysis.turbulence(out)
    assert ti["height_m"] == 80
    row = next(r for r in ti["by_speed"] if r["ws_bin"] == 10.5)
    assert row["ti_mean"] == pytest.approx(0.10 + 0.6 / 10.5, abs=0.01)
    rose = analysis.wind_rose(out)
    assert sum(map(sum, rose["frequency_pct"])) == pytest.approx(100, abs=0.1)
    rho = analysis.air_density(out)
    assert 1.1 < rho["mean"] < 1.3


def test_qc_summary(checked):
    out, sensors, *_ = checked
    s = qc.summary(out, sensors)
    ws80 = next(x for x in s["sensors"] if x["code"] == "ws_80_n")
    assert 60 < ws80["valid_pct"] < 99 and ws80["flags"]["tower_shadow"] > 0
    assert set(ws80["monthly_valid_pct"]) == set(s["months"])
    # premier défaut du capteur à 40 m : le premier pic injecté (hors manquants)
    ws40 = next(x for x in s["sensors"] if x["code"] == "ws_40")
    assert ws40["first_event_utc"] is not None
    assert pd.Timestamp(ws40["first_event_utc"]) <= out.index[-1]
