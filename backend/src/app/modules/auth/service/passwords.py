"""Passwords (ADR api-and-auth-baseline D6, NIST SP 800-63B-4): normalized with NFKC, checked against a list of common
passwords, stored as argon2id.

common-passwords.txt is SecLists Passwords/Common-Credentials/Pwdb_top-1000000.txt at commit
12274c98fdebe98c7a7284914436a472ed469aed (sha256 e9a88f67aafe65496682dc374559ee714e978bee50314767494c3e37a18c9fc8,
MIT, see LICENSE-SecLists), keeping only the distinct lines of at least 15 code points: shorter ones can never be a
valid password here. To refresh it, download the file at a new commit, check its hash, and keep the lines of at least
15 Unicode code points (not bytes), in their original order. The Go template ships the same file.
"""

import asyncio
import functools
import importlib.resources
import os
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING

import argon2
from argon2.exceptions import InvalidHashError, VerificationError

if TYPE_CHECKING:
    from collections.abc import Callable


def normalize_password(password: str) -> str:
    """NFKC, applied before a password is checked against the list or hashed, so the same password typed with
    full-width or half-width characters is the same password. The contract's limits count the raw input; the service
    also counts the minimum on this form, which can be shorter (e plus a combining acute becomes one é)."""
    return unicodedata.normalize("NFKC", password)


# NIST SP 800-63B-4: at least 15 characters, counted on the normalized form that is hashed. It equals the contract's
# password minimum, which tests/test_passwords.py checks.
PASSWORD_MIN_LENGTH = 15


@functools.cache
def _common_passwords() -> frozenset[str]:
    text = importlib.resources.files(__package__).joinpath("common-passwords.txt").read_text(encoding="utf-8")
    return frozenset(normalize_password(line) for line in text.splitlines() if line)


def is_common_password(normalized: str) -> bool:
    """Whether the NFKC-normalized password is on the list. The match is exact: the list already holds the common
    case variants."""
    return normalized in _common_passwords()


def common_password_count() -> int:
    return len(_common_passwords())


@dataclass(frozen=True, slots=True)
class Argon2Params:
    memory_kib: int
    time_cost: int
    parallelism: int


# OWASP Password Storage Cheat Sheet's argon2id minimum: 19 MiB, 2 iterations, 1 thread.
OWASP_ARGON2 = Argon2Params(memory_kib=19 * 1024, time_cost=2, parallelism=1)

_ARGON2ID = "$argon2id$"


class PasswordHasher:
    """argon2id hashes as PHC strings ($argon2id$v=19$m=…,t=…,p=…$salt$key).

    Hashing is CPU-bound and holds memory_kib while it runs, so it runs on a pool of its own with at most
    max_concurrent threads (default: the CPUs this process may use; argon2-cffi releases the GIL). The pool's size is
    the bound: a request that stops waiting cancels its hash if it has not started, and a started hash keeps its
    thread until it finishes. The event loop is never blocked by a hash.
    """

    def __init__(self, params: Argon2Params = OWASP_ARGON2, max_concurrent: int | None = None) -> None:
        self._argon2 = argon2.PasswordHasher(
            time_cost=params.time_cost,
            memory_cost=params.memory_kib,
            parallelism=params.parallelism,
            hash_len=32,
            salt_len=16,
            type=argon2.Type.ID,
        )
        workers = max_concurrent or os.process_cpu_count() or 1
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="argon2")

    async def hash(self, password: str) -> str:
        return await self._run(self._argon2.hash, password)

    async def verify(self, password: str, encoded: str) -> bool:
        """Whether password matches encoded, recomputed with the parameters stored in encoded so hashes made with
        older parameters still verify. Anything but a well-formed argon2id hash is a mismatch."""
        if not encoded.startswith(_ARGON2ID):
            return False
        return await self._run(self._matches, password, encoded)

    def close(self) -> None:
        """Stops the pool: queued hashes are cancelled, running ones finish on their own."""
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _matches(self, password: str, encoded: str) -> bool:
        try:
            return self._argon2.verify(encoded, password)
        except (
            VerificationError,
            InvalidHashError,
        ):  # a wrong password (VerifyMismatchError) or a hash that does not parse
            return False

    async def _run[T](self, fn: Callable[..., T], *args: str) -> T:
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)
