import json
from pathlib import Path

import pytest

from mtg_manager.config import Config, load_config
from mtg_manager.db import get_conn, insert_built_deck, upsert_cards
from mtg_manager.models import OwnedCard
from web.export import export_static


def _cfg(tmp_path, web_static_dir=None):
    return Config(
        packages=[],
        moxfield_delay=0.0,
        mtgtop8_delay=0.0,
        mtgtop8_cache_ttl=0,
        db_path=tmp_path / "test.db",
        web_static_dir=web_static_dir,
    )


def test_config_web_static_dir_optional(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text(
        "[moxfield]\npackages = []\nrequest_delay_seconds = 1.0\n"
        "[mtgtop8]\nrequest_delay_seconds = 1.5\ncache_ttl_hours = 24\n"
        "[database]\npath = '/tmp/test.db'\n"
    )
    cfg = load_config(toml)
    assert cfg.web_static_dir is None


def test_config_web_static_dir_loaded(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text(
        "[moxfield]\npackages = []\nrequest_delay_seconds = 1.0\n"
        "[mtgtop8]\nrequest_delay_seconds = 1.5\ncache_ttl_hours = 24\n"
        "[database]\npath = '/tmp/test.db'\n"
        "[web]\nstatic_dir = '/var/www/mtg'\n"
    )
    cfg = load_config(toml)
    assert cfg.web_static_dir == Path("/var/www/mtg")
