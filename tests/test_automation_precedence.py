"""Precedence matrix for the automation kill switch and observe mode.

Safety invariant under test: NO "enabling" value in one layer may neutralise
a valid stop command in another layer. Every valid stop wins over every path.

Matrix (env ZERO_AUTOMATION_DISABLED x persisted automation_enabled):
    env unset   : stored None->allow, true->allow, false/invalid/error->block
    env "true"  : ALWAYS block (env_kill_switch)
    env "false" : identical to unset (an explicit false is neutral, never enabling)
    env invalid : ALWAYS block (conservative -- see kill_switch_active docstring)

Also covered: observe-mode coexistence, live setting changes, and concurrent
workers seeing consistent per-call results.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from zero import automation


class FakeStore:
    def __init__(self, value=None, error: Exception | None = None):
        self.value = value
        self.error = error

    async def get_setting(self, key: str):
        if self.error is not None:
            raise self.error
        return self.value


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(automation.KILL_ENV, raising=False)
    monkeypatch.delenv(automation.OBSERVE_ENV, raising=False)


# ---------------------------------------------------------- precedence matrix

@pytest.mark.parametrize(
    "env_value,stored,expected",
    [
        # env unset
        (None, None, None),
        (None, "true", None),
        (None, "TRUE", None),
        (None, "false", "setting_disabled"),
        (None, "0", "setting_disabled"),
        (None, "maybe", "setting_invalid"),
        (None, RuntimeError("db down"), "setting_read_error"),
        # env explicitly true: every stored state blocked
        ("true", None, "env_kill_switch"),
        ("true", "true", "env_kill_switch"),
        ("true", "false", "env_kill_switch"),
        ("true", "maybe", "env_kill_switch"),
        ("true", RuntimeError("x"), "env_kill_switch"),
        # env explicitly false: neutral, never enables a stopped setting
        ("false", None, None),
        ("false", "true", None),
        ("false", "false", "setting_disabled"),
        ("false", "maybe", "setting_invalid"),
        ("false", RuntimeError("x"), "setting_read_error"),
        # env invalid value: conservative block regardless of storage
        ("maybe", None, "env_kill_switch"),
        ("maybe", "true", "env_kill_switch"),
        ("maybe", "false", "env_kill_switch"),
        ("yes please", "true", "env_kill_switch"),
    ],
)
async def test_precedence_matrix(env_value, stored, expected, monkeypatch):
    if env_value is not None:
        monkeypatch.setenv(automation.KILL_ENV, env_value)
    store = FakeStore(stored) if not isinstance(stored, Exception) else FakeStore(error=stored)
    assert await automation.automation_disabled(store) == expected


async def test_no_store_without_env_allows():
    """Fresh install, no env, no store access yet: default enabled."""
    assert await automation.automation_disabled(None) is None


# ---------------------------------------------------------- observe mode

async def test_observe_mode_does_not_bypass_kill(monkeypatch):
    """Observe-only must never act as an enabler: with the kill switch on,
    the gate still blocks (observe only suppresses sending independently)."""
    monkeypatch.setenv(automation.OBSERVE_ENV, "true")
    assert automation.observe_only() is True
    monkeypatch.setenv(automation.KILL_ENV, "true")
    assert await automation.automation_disabled(FakeStore("true")) == "env_kill_switch"
    # And observe alone never blocks the gate itself (it is not a kill layer).
    monkeypatch.delenv(automation.KILL_ENV)
    assert await automation.automation_disabled(FakeStore("true")) is None


# ---------------------------------------------------------- runtime changes

async def test_setting_change_is_picked_up_live():
    """The gate reads storage on EVERY decision: flipping the persisted value
    takes effect immediately without restart."""
    store = FakeStore("true")
    assert await automation.automation_disabled(store) is None
    store.value = "false"
    assert await automation.automation_disabled(store) == "setting_disabled"
    store.value = None
    assert await automation.automation_disabled(store) is None


async def test_transient_read_failure_blocks_then_recovers():
    store = FakeStore("true")
    assert await automation.automation_disabled(store) is None
    store.error = RuntimeError("transient")
    assert await automation.automation_disabled(store) == "setting_read_error"
    store.error = None
    assert await automation.automation_disabled(store) is None


# ---------------------------------------------------------- concurrency

async def test_concurrent_workers_consistent():
    """20 workers x 25 calls against two different stores: each call sees only
    its own store's state; no cross-talk, no partial reads."""
    enabled_store = FakeStore("true")
    disabled_store = FakeStore("false")

    async def worker(store, expect, n=25):
        for _ in range(n):
            result = await automation.automation_disabled(store)
            assert result == expect

    await asyncio.gather(*[
        worker(enabled_store, None) for _ in range(10)
    ] + [
        worker(disabled_store, "setting_disabled") for _ in range(10)
    ])


async def test_concurrent_kill_flip_never_half_applies(monkeypatch):
    """While workers hammer the gate, flipping the env kill switch yields only
    fully-blocked or fully-allowed answers -- never an inconsistent one."""
    store = FakeStore("true")
    stop = asyncio.Event()

    async def hammer():
        while not stop.is_set():
            result = await automation.automation_disabled(store)
            assert result in {None, "env_kill_switch"}
            # automation_disabled has no internal await points; yield explicitly
            # so the flipper coroutine gets scheduled (otherwise this loop
            # starves the event loop and deadlocks the test itself).
            await asyncio.sleep(0)

    tasks = [asyncio.create_task(hammer()) for _ in range(5)]
    try:
        for value in ("true", "false", "true", "false"):
            monkeypatch.setenv(automation.KILL_ENV, value)
            await asyncio.sleep(0.01)
    finally:
        stop.set()
        await asyncio.gather(*tasks)