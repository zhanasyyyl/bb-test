from django.contrib.auth.hashers import PBKDF2PasswordHasher


class FastPBKDF2PasswordHasher(PBKDF2PasswordHasher):
    """PBKDF2-SHA256 with 200,000 iterations instead of Django's 1,000,000.

    Sign-in is CPU-bound (one hash per attempt), so this makes each sign-in
    about 5x cheaper: roughly 8 -> 40 per second on a 4-vCPU Contabo VPS.
    The trade-off is that leaked hashes are about 5x faster to crack.

    It keeps the "pbkdf2_sha256" algorithm name, so it verifies existing
    1,000,000-iteration hashes and re-hashes them at 200,000 on each user's
    next successful sign-in. It must replace Django's PBKDF2PasswordHasher in
    PASSWORD_HASHERS, not sit next to it: two hashers with the same algorithm
    name would make every sign-in re-hash the password.
    """

    iterations = 200_000
