"""Tests for the ``mouthflow list-kits`` command (machine-facing JSON)."""

from __future__ import annotations

import json
import socket

from typer.testing import CliRunner

from mouthflow import cli as cli_module
from test_execute import FakeAbleton

runner = CliRunner()


def _drums(n: int) -> dict:
    return {
        "status": "ok",
        "result": {
            "path": "Drums",
            "items": [
                {
                    "name": f"Kit {i}",
                    "is_folder": False,
                    "is_loadable": True,
                    "uri": f"query:Drums#FileId_{i}",
                }
                for i in range(n)
            ],
        },
    }


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_list_kits_outputs_json_array():
    with FakeAbleton([_drums(3)]) as fake:
        result = runner.invoke(cli_module.app, ["list-kits", "--port", str(fake.port)])
    assert result.exit_code == 0, result.output
    kits = json.loads(result.output[result.output.index("[") :])
    assert kits == [
        {"name": "Kit 0", "uri": "query:Drums#FileId_0"},
        {"name": "Kit 1", "uri": "query:Drums#FileId_1"},
        {"name": "Kit 2", "uri": "query:Drums#FileId_2"},
    ]


def test_list_kits_respects_limit():
    with FakeAbleton([_drums(10)]) as fake:
        result = runner.invoke(
            cli_module.app, ["list-kits", "--port", str(fake.port), "--limit", "3"]
        )
    assert result.exit_code == 0, result.output
    kits = json.loads(result.output[result.output.index("[") :])
    assert len(kits) == 3
    assert kits[0]["name"] == "Kit 0"  # strided sample keeps order, starts at head


def test_list_kits_fails_when_unreachable():
    result = runner.invoke(cli_module.app, ["list-kits", "--port", str(_free_port())])
    assert result.exit_code == 1
    assert "not reachable" in result.output
