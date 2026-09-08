"""The exploratory probe must never pair or send device commands."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

spec = importlib.util.spec_from_file_location(
    "bhyve_probe", Path(__file__).resolve().parents[2] / "deploy/bhyve_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Scanner:
    @staticmethod
    async def discover(**kwargs):
        return {"test": (NS(address="AA:BB", name="timer"),
                         NS(service_uuids=[probe.BHYVE_SERVICE], local_name=None, rssi=-60))}


def test_scan_never_connects():
    def forbidden(*args, **kwargs):
        pytest.fail("scan must not connect")
    result = asyncio.run(probe.probe(Scanner, forbidden))
    assert len(result["candidates"]) == 1
    assert result["connected"] is False


def test_services_only_and_disconnect():
    calls = []
    class Client:
        def __init__(self, device, *, timeout, pair):
            assert pair is False
            assert device.address == "AA:BB"
            self.services = [NS(uuid=probe.BHYVE_SERVICE,
                                characteristics=[NS(uuid=u) for u in probe.EXPECTED_CHARS])]
        async def __aenter__(self):
            calls.append("connect")
            return self
        async def __aexit__(self, *args):
            calls.append("disconnect")
        def __getattr__(self, name):
            pytest.fail(f"Unexpected BLE operation: {name}")
    result = asyncio.run(probe.probe(Scanner, Client, address="aa:bb"))
    assert calls == ["connect", "disconnect"]
    assert all(result["expected_characteristics"].values())
    assert result["protocol_compatibility"] == "untested"


def test_unknown_target_never_connects():
    def forbidden(*args, **kwargs):
        pytest.fail("unknown target must not connect")
    with pytest.raises(RuntimeError, match="not observed"):
        asyncio.run(probe.probe(Scanner, forbidden, address="CC:DD"))


def test_named_device_without_advertised_services():
    class NameScanner:
        @staticmethod
        async def discover(**kwargs):
            return {"test": (NS(address="AA:BB", name="bhyve_example"),
                             NS(service_uuids=[], local_name=None, rssi=-60))}
    def forbidden(*args, **kwargs):
        pytest.fail("name discovery must not connect")
    result = asyncio.run(probe.probe(NameScanner, forbidden))
    assert result["candidates"][0]["name"] == "bhyve_example"
    assert result["protocol_compatibility"] == "untested"
