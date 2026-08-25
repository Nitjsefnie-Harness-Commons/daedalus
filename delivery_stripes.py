"""Choose stable, caller-unsteerable stripes for delivery-result locks.

The mapping must stay stable within one process so callers naming the same
target take the same lock, but whoever chooses target names must not be able to
compute collisions offline. A per-process secret key satisfies both; the fixed
stripe count keeps the lock table bounded instead of growing with target names.
"""
import hashlib
import secrets


_SEED = secrets.token_bytes(32)


def stripe_index(key, stripes):
    """Return the keyed stripe index for a caller-produced byte key."""
    # BLAKE2's keyed digest makes target-name collisions infeasible to choose
    # without this process's seed, while repeated calls still share one map.
    digest = hashlib.blake2b(key, digest_size=8, key=_SEED).digest()
    return int.from_bytes(digest, 'big') % stripes
