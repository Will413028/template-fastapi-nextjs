"""Password storage and the rules for new passwords (Go version: internal/modules/auth/service_test.go TestArgon2Hash
and TestArgonConcurrencyBounded, internal/modules/auth/blocklist/blocklist_test.go TestContains).

ADR api-and-auth-baseline: argon2id with OWASP's minimum parameters, NFKC before checking or hashing, a list of common
passwords (NIST SP 800-63B-4).
"""

import asyncio
import threading
import time

import pytest

from app.modules.auth.service.passwords import (
    OWASP_ARGON2,
    Argon2Params,
    PasswordHasher,
    common_password_count,
    is_common_password,
    normalize_password,
)

CHEAP = Argon2Params(memory_kib=64, time_cost=1, parallelism=1)


def test_hash_uses_owasp_argon2id_and_verifies() -> None:
    hasher = PasswordHasher(OWASP_ARGON2)
    encoded = asyncio.run(hasher.hash("correct horse battery staple"))
    assert encoded.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert asyncio.run(hasher.verify("correct horse battery staple", encoded))
    assert not asyncio.run(hasher.verify("correct horse battery stapler", encoded))


def test_verify_reads_the_parameters_stored_in_the_hash() -> None:
    other = asyncio.run(PasswordHasher(CHEAP).hash("pw"))
    assert asyncio.run(PasswordHasher(OWASP_ARGON2).verify("pw", other))


@pytest.mark.parametrize(
    "encoded",
    [
        "",
        "$2b$12$abc",
        "$argon2id$v=19$m=0,t=1,p=1$c2FsdA$a2V5",
        "$argon2id$v=19$m=64,t=1,p=1$!$a2V5",
        "$argon2id$v=19$m=64,t=1,p=1$c2FsdA$",
    ],
)
def test_a_hash_that_does_not_parse_is_a_mismatch(encoded: str) -> None:
    assert not asyncio.run(PasswordHasher(CHEAP).verify("pw", encoded))


def test_only_argon2id_hashes_verify() -> None:
    hasher = PasswordHasher(CHEAP)
    encoded = asyncio.run(hasher.hash("pw"))
    # the same hash relabelled argon2i: argon2-cffi alone would verify it as that variant
    assert not asyncio.run(hasher.verify("pw", encoded.replace("$argon2id$", "$argon2i$", 1)))


class SlowArgon2:
    """Stands in for argon2.PasswordHasher: each hash takes a while and records how many run at once."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = 0
        self.peak = 0
        self.calls = 0
        self.release = threading.Event()
        self.release.set()
        self.two_started = threading.Event()
        # every hash waits, up to a second, until two run at once: the peak then shows the bound, not the timing
        self.overlap = threading.Event()

    def hash(self, password: str) -> str:
        with self.lock:
            self.calls += 1
            self.running += 1
            self.peak = max(self.peak, self.running)
            if self.calls == 2:
                self.two_started.set()
            if self.running >= 2:  # two at once is what the bound allows
                self.overlap.set()
        self.overlap.wait(1)
        self.release.wait(5)
        time.sleep(0.005)
        with self.lock:
            self.running -= 1
        return "$argon2id$" + password


def test_concurrent_hashes_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    # each argon2 hash holds 19 MiB while it runs; without a bound a burst of sign-ins holds that much per request
    hasher = PasswordHasher(CHEAP, max_concurrent=2)
    slow = SlowArgon2()
    monkeypatch.setattr(hasher, "_argon2", slow)

    async def burst() -> None:
        await asyncio.gather(*(hasher.hash("pw") for _ in range(8)))

    asyncio.run(burst())
    assert slow.peak == 2


def test_a_cancelled_waiter_does_not_free_a_running_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    hasher = PasswordHasher(CHEAP, max_concurrent=2)
    slow = SlowArgon2()
    slow.release.clear()
    monkeypatch.setattr(hasher, "_argon2", slow)
    try:
        started_while_busy = asyncio.run(cancel_while_busy(hasher, slow))
    finally:
        slow.release.set()  # a failing run must not leave threads waiting: asyncio.run waits for them on exit
    assert started_while_busy == 2  # neither the cancelled waiter nor a later hash started while both slots were busy
    assert slow.peak == 2
    assert slow.calls == 3  # the cancelled waiter never ran


async def cancel_while_busy(hasher: PasswordHasher, slow: SlowArgon2) -> int:
    """Fills both slots, cancels every request (the two holding a slot and one waiting), then asks for another hash;
    returns how many hashes had started before the slots were released."""
    running = [asyncio.create_task(hasher.hash("pw")) for _ in range(2)]
    assert await asyncio.to_thread(slow.two_started.wait, 5)  # both slots taken
    waiting = asyncio.create_task(hasher.hash("pw"))
    await asyncio.sleep(0.01)
    # the requests holding the slots give up: their hashes keep running, so the slots stay taken
    for task in running:
        task.cancel()
    waiting.cancel()
    late = asyncio.create_task(hasher.hash("pw"))
    await asyncio.sleep(0.05)
    started = slow.calls
    slow.release.set()
    await late
    with pytest.raises(asyncio.CancelledError):
        await waiting
    return started


def test_normalize_folds_full_width_characters() -> None:
    full_width = "".join(chr(ord(c) + 0xFEE0) for c in "correct-horse")  # U+FF43 U+FF4F … U+FF0D …
    assert normalize_password(full_width) == "correct-horse"


def test_common_password_list() -> None:
    # 5406 lines of at least 15 code points (SecLists Pwdb, see the module docstring); NFKC folds none together
    assert common_password_count() == 5406
    assert is_common_password("demon1q2w3e4r5t")
    assert not is_common_password("correct horse battery staple")
