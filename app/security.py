import re
import time
from collections import defaultdict
from typing import Tuple, Dict, List
from fastapi import HTTPException, Request

# Team name rules:
# Allowed: A-Z, a-z, 0-9, !, ?, @, _
# Numbers 0-9 explicitly allowed
# Spaces strictly disallowed
TEAM_NAME_REGEX = re.compile(r"^[A-Za-z0-9!?@_]{1,30}$")

def validate_team_name(name: str) -> Tuple[bool, str]:
    """
    Validates team name according to Section 8 rules:
    - Allowed characters: A-Z, a-z, 0-9, !, ?, @, _
    - Spaces are NOT allowed.
    - Length between 1 and 30 characters.
    """
    if not name:
        return False, "Team name cannot be empty."
    if " " in name:
        return False, "Team names cannot contain spaces."
    if not TEAM_NAME_REGEX.match(name):
        return False, "Team name contains invalid characters. Allowed: letters, numbers (0-9), !, ?, @, _"
    return True, ""

def sanitize_text(text: str, max_length: int = 100) -> str:
    """Sanitizes text by stripping whitespace and removing control characters."""
    if not text:
        return ""
    cleaned = "".join(ch for ch in text if ch.isprintable())
    return cleaned.strip()[:max_length]

class InMemoryRateLimiter:
    """
    In-memory rate limiter to mitigate brute-force attacks on login/registration.
    Keyed by client IP or identifier.
    """
    def __init__(self, max_attempts: int = 10, window_seconds: int = 60):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.attempts: Dict[str, List[float]] = defaultdict(list)

    def check(self, key: str) -> None:
        now = time.time()
        # Clean older records
        self.attempts[key] = [t for t in self.attempts[key] if now - t < self.window_seconds]
        if len(self.attempts[key]) >= self.max_attempts:
            raise HTTPException(
                status_code=429,
                detail="Too many attempts. Please wait a minute before retrying."
            )
        self.attempts[key].append(now)

rate_limiter = InMemoryRateLimiter(max_attempts=30, window_seconds=60)
registration_rate_limiter = InMemoryRateLimiter(max_attempts=30, window_seconds=60)
login_rate_limiter = InMemoryRateLimiter(max_attempts=20, window_seconds=60)
admin_rate_limiter = InMemoryRateLimiter(max_attempts=10, window_seconds=60)
answer_rate_limiter = InMemoryRateLimiter(max_attempts=120, window_seconds=60)
submission_rate_limiter = InMemoryRateLimiter(max_attempts=10, window_seconds=60)
violation_rate_limiter = InMemoryRateLimiter(max_attempts=30, window_seconds=60)



