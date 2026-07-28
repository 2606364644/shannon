"""BrandingStore:品牌名运行时覆盖的落盘/读取/校验/清除。"""
from __future__ import annotations

import json

import pytest

from supernova_web.components.branding_store import (
    BRANDING_FILENAME,
    MAX_BRAND_NAME,
    BrandingStore,
)


def test_get_brand_name_no_file_returns_none(tmp_path):
    s = BrandingStore(tmp_path)
    assert s.get_brand_name() is None


def test_set_then_get_roundtrip(tmp_path):
    s = BrandingStore(tmp_path)
    assert s.set_brand_name("Acme Security") == "Acme Security"
    assert s.get_brand_name() == "Acme Security"
    # 落盘文件存在且内容正确
    data = json.loads((tmp_path / BRANDING_FILENAME).read_text(encoding="utf-8"))
    assert data == {"brand_name": "Acme Security"}


def test_set_trims_whitespace(tmp_path):
    s = BrandingStore(tmp_path)
    assert s.set_brand_name("  Padded  ") == "Padded"
    assert s.get_brand_name() == "Padded"


def test_set_empty_string_clears_override(tmp_path):
    s = BrandingStore(tmp_path)
    s.set_brand_name("Acme")
    assert s.set_brand_name("   ") is None
    assert s.get_brand_name() is None  # 清除 → 回落 env/default


def test_set_none_clears_override(tmp_path):
    s = BrandingStore(tmp_path)
    s.set_brand_name("Acme")
    assert s.set_brand_name(None) is None
    assert s.get_brand_name() is None


def test_get_tolerates_corrupt_json(tmp_path):
    (tmp_path / BRANDING_FILENAME).write_text("{ not json", encoding="utf-8")
    s = BrandingStore(tmp_path)
    assert s.get_brand_name() is None  # 损坏不当机,回落


def test_get_tolerates_missing_key(tmp_path):
    (tmp_path / BRANDING_FILENAME).write_text("{}", encoding="utf-8")
    assert BrandingStore(tmp_path).get_brand_name() is None


def test_get_tolerates_non_string_value(tmp_path):
    (tmp_path / BRANDING_FILENAME).write_text('{"brand_name": 123}', encoding="utf-8")
    assert BrandingStore(tmp_path).get_brand_name() is None


def test_validate_rejects_empty():
    with pytest.raises(ValueError):
        BrandingStore.validate("   ")


def test_validate_rejects_too_long():
    with pytest.raises(ValueError):
        BrandingStore.validate("x" * (MAX_BRAND_NAME + 1))


def test_validate_accepts_max_length():
    assert BrandingStore.validate("x" * MAX_BRAND_NAME) == "x" * MAX_BRAND_NAME


def test_validate_rejects_non_string():
    with pytest.raises(ValueError):
        BrandingStore.validate(123)  # type: ignore[arg-type]


def test_set_creates_missing_dir(tmp_path):
    s = BrandingStore(tmp_path / "nested" / "ws")
    assert s.set_brand_name("Acme") == "Acme"
    assert s.get_brand_name() == "Acme"
