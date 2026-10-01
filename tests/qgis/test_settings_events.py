"""hamq.settings (QgsSettings under hamq/) and hamq.events (process-wide signals)."""

from __future__ import annotations

import os
import threading

import pytest
from qgis.core import QgsApplication, QgsSettings
from qgis.PyQt.QtCore import QCoreApplication, QObject

from hamq import events as events_module
from hamq import settings as settings_module
from hamq.core import maidenhead
from hamq.events import HamQEvents, events
from hamq.settings import HamQSettings, cty_cache_path, default_gpkg_path, profile_dir

# property -> (default, a different valid value, the value read back)
PROPERTIES = {
    "my_call": ("", " yu1ab ", "YU1AB"),
    "my_grid": ("", " kn04FT ", "KN04ft"),
    "wsjtx_addr": ("127.0.0.1", "224.0.0.1", "224.0.0.1"),
    "wsjtx_port": (2237, 2238, 2238),
    "wsjtx_autostart": (False, True, True),
    "language": ("auto", "sr_Cyrl", "sr_Cyrl"),
    "last_serbian": ("sr_Latn", "sr_Cyrl", "sr_Cyrl"),
    "cty_downloaded": ("", "2026-09-29", "2026-09-29"),
    "rig_enabled": (False, True, True),
    "rig_host": ("127.0.0.1", "192.168.1.20", "192.168.1.20"),
    "rig_port": (4532, 4534, 4534),
    "rig_poll_ms": (1000, 500, 500),
    "rot_enabled": (False, True, True),
    "rot_host": ("127.0.0.1", "rotator.local", "rotator.local"),
    "rot_port": (4533, 4535, 4535),
    "rot_min_az": (0.0, -180.0, -180.0),
    "rot_max_az": (360.0, 450.0, 450.0),
    "rot_confirmed": (False, True, True),
}


@pytest.fixture
def settings(clean_settings):
    return HamQSettings()


def test_settings_use_the_temporary_test_profile():
    config_dir = os.path.realpath(os.environ["QGIS_CUSTOM_CONFIG_PATH"])
    assert os.path.realpath(QgsSettings().fileName()).startswith(config_dir)


@pytest.mark.parametrize("name", sorted(PROPERTIES))
def test_default(settings, name):
    default = PROPERTIES[name][0]
    value = getattr(settings, name)
    assert value == default
    assert type(value) is type(default)


def test_gpkg_path_default(settings):
    assert settings.gpkg_path == default_gpkg_path()


@pytest.mark.parametrize("name", sorted(PROPERTIES))
def test_round_trip(settings, name):
    _default, value, expected = PROPERTIES[name]
    setattr(settings, name, value)
    assert getattr(settings, name) == expected
    assert getattr(HamQSettings(), name) == expected  # a new instance sees it
    assert QgsSettings().value(f"hamq/{name}") is not None  # stored under hamq/


def test_gpkg_path_round_trip(settings, tmp_path):
    path = str(tmp_path / "my log.gpkg")
    settings.gpkg_path = f"  {path} "
    assert settings.gpkg_path == path


@pytest.mark.parametrize(
    ("name", "raw", "expected"),
    [
        ("wsjtx_port", "2240", 2240),
        ("wsjtx_port", "abc", 2237),
        ("wsjtx_port", 70000, 2237),
        ("wsjtx_port", 0, 2237),
        ("rig_poll_ms", -5, 1000),
        ("wsjtx_autostart", "true", True),
        ("wsjtx_autostart", "false", False),
        ("wsjtx_autostart", "maybe", False),
        ("rot_confirmed", 1, True),
        ("rot_max_az", "450.5", 450.5),
        ("rot_max_az", "north", 360.0),
        ("rot_min_az", "nan", 0.0),
        ("last_serbian", "en", "sr_Latn"),
        ("last_serbian", " sr_Cyrl ", "sr_Cyrl"),
        ("last_serbian", "", "sr_Latn"),
        ("my_call", 12, "12"),
        ("my_call", " yu1ab ", "YU1AB"),
        ("my_grid", " kn04FT12ab ", "KN04ft12"),
        ("language", "", "auto"),
        ("language", "klingon", "auto"),
        ("language", " SR-latn ", "sr_Latn"),
        ("wsjtx_addr", "", "127.0.0.1"),
        ("wsjtx_addr", "   ", "127.0.0.1"),
        ("rig_host", "", "127.0.0.1"),
        ("rot_host", "  ", "127.0.0.1"),
    ],
)
def test_stored_values_are_converted_or_defaulted(settings, name, raw, expected):
    QgsSettings().setValue(f"hamq/{name}", raw)
    assert getattr(settings, name) == expected


@pytest.mark.parametrize("raw", ["", "   "])
def test_empty_stored_gpkg_path_reads_as_default(settings, raw):
    # e.g. a hand-edited "gpkg_path=" line in the QGIS settings file
    QgsSettings().setValue("hamq/gpkg_path", raw)
    assert settings.gpkg_path == default_gpkg_path()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("wsjtx_port", 0),
        ("wsjtx_port", 65536),
        ("wsjtx_port", "abc"),
        ("rig_port", -1),
        ("rig_poll_ms", 0),
        ("last_serbian", "en"),
        ("last_serbian", ""),
        ("rot_max_az", "north"),
        ("wsjtx_autostart", "maybe"),
        ("gpkg_path", ""),
        ("gpkg_path", "   "),
        ("wsjtx_addr", ""),
        ("wsjtx_addr", "  "),
        ("rig_host", ""),
        ("rot_host", " "),
        ("language", ""),
        ("language", "klingon"),
    ],
)
def test_invalid_values_are_rejected(settings, name, value):
    before = getattr(settings, name)
    with pytest.raises(ValueError):
        setattr(settings, name, value)
    assert getattr(settings, name) == before
    assert QgsSettings().value(f"hamq/{name}") is None  # nothing was stored


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("auto", "auto"),
        ("en", "en"),
        ("sr_Latn", "sr_Latn"),
        ("sr_Cyrl", "sr_Cyrl"),
        (" SR_CYRL ", "sr_Cyrl"),
        ("sr-Latn", "sr_Latn"),
        ("EN", "en"),
    ],
)
def test_language_is_stored_as_a_canonical_code(settings, value, expected):
    settings.language = value
    assert settings.language == expected
    assert QgsSettings().value("hamq/language") == expected


def test_last_serbian_is_stripped(settings):
    settings.last_serbian = " sr_Cyrl "
    assert settings.last_serbian == "sr_Cyrl"


def test_grid_normalization(settings):
    settings.my_grid = "jn94"
    assert settings.my_grid == "JN94"
    settings.my_grid = "kn04ft45"
    assert settings.my_grid == "KN04ft45"
    settings.my_grid = " not a grid "
    assert settings.my_grid == "not a grid"


@pytest.mark.parametrize("value", ["KN04FT12AB", " kn04ft12ab ", "kn04FT", "jn", "JN94ab"])
def test_grid_normalization_matches_core(settings, value):
    """The stored locator is what hamq.core.maidenhead.normalize() gives (10 chars -> 8)."""
    settings.my_grid = value
    assert settings.my_grid == maidenhead.normalize(value)


def test_profile_dir_is_created_inside_the_qgis_profile(qgis_app):
    base = os.path.realpath(QgsApplication.qgisSettingsDirPath())
    path = profile_dir()
    assert os.path.isdir(path)
    assert os.path.basename(path) == "hamq"
    assert os.path.realpath(path).startswith(base)
    os.rmdir(path)  # created again on demand
    assert os.path.isdir(profile_dir())


def test_file_paths():
    assert default_gpkg_path() == os.path.join(profile_dir(), "hamq.gpkg")
    assert cty_cache_path() == os.path.join(profile_dir(), "cty.dat")


def test_settings_prefix():
    assert settings_module.SETTINGS_PREFIX == "hamq/"


def test_station(settings):
    qso = pytest.importorskip("hamq.core.qso", reason="core/qso.py is not written yet")
    settings.my_call = "yu1ab"
    settings.my_grid = "kn04ft"
    station = settings.station()
    assert isinstance(station, qso.Station)
    assert (station.call, station.grid) == ("YU1AB", "KN04ft")


# --- events -------------------------------------------------------------------------


def test_events_singleton():
    first = events()
    assert isinstance(first, HamQEvents)
    assert isinstance(first, QObject)
    assert events() is first


def test_events_live_in_the_main_thread():
    assert events().thread() is QCoreApplication.instance().thread()


def test_signals_deliver_arguments():
    received = []
    obj = events()

    def on_data(path):
        received.append(("data", path))

    def on_language(language):
        received.append(("language", language))

    def on_settings():
        received.append(("settings",))

    obj.dataChanged.connect(on_data)
    obj.languageChanged.connect(on_language)
    obj.settingsChanged.connect(on_settings)
    try:
        obj.dataChanged.emit("/tmp/hamq.gpkg")
        obj.languageChanged.emit("sr_Cyrl")
        obj.settingsChanged.emit()
    finally:
        obj.dataChanged.disconnect(on_data)
        obj.languageChanged.disconnect(on_language)
        obj.settingsChanged.disconnect(on_settings)
    assert received == [("data", "/tmp/hamq.gpkg"), ("language", "sr_Cyrl"), ("settings",)]


def test_first_call_from_a_worker_thread_still_lives_in_main_thread(monkeypatch):
    monkeypatch.setattr(events_module, "_instance", None)
    created = []
    worker = threading.Thread(target=lambda: created.append(events_module.events()))
    worker.start()
    worker.join()
    assert len(created) == 1
    assert created[0].thread() is QCoreApplication.instance().thread()
    assert events_module.events() is created[0]
