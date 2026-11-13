"""M0-AC1: the repo carries the MIT license that plugin.json declares."""

HOLDERS = "Copyright (c) 2026 Kumaran C S and Gunanithi CS"


def _license(root):
    path = root / "LICENSE"
    assert path.is_file(), "missing LICENSE at the plugin root"
    return path.read_text()


def test_plugin_json_declares_mit(plugin_json):
    assert plugin_json["license"] == "MIT"


def test_license_is_the_mit_text(root):
    text = _license(root)
    assert text.splitlines()[0] == "MIT License"
    assert "Permission is hereby granted, free of charge, to any person obtaining a copy" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND' in text


def test_license_names_the_upstream_copyright_holders(root):
    assert HOLDERS in _license(root).splitlines()
