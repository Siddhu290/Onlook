"""Pure decision state machine: (now, tracks, owner_enrolled) -> actions. No I/O, no clock."""
from __future__ import annotations

from dataclasses import dataclass

WARN, LOCK, AWAY_LOCK = "WARN", "LOCK", "AWAY_LOCK"


@dataclass(frozen=True)
class Action:
    kind: str
    reason: str = ""


class Policy:
    def __init__(self, warn_after: float, lock_after: float, grace: float,
                 cooldown: float, away_lock: float):
        self.warn_after, self.lock_after, self.grace = warn_after, lock_after, grace
        self.cooldown, self.away_lock = cooldown, away_lock
        self.cooldown_until = float("-inf")
        self.owner_seen: float | None = None
        self.away_armed = True
        self._end_episode()

    def _end_episode(self) -> None:
        self.episode_start: float | None = None
        self.last_onlooker = 0.0
        self.warned = False

    def reset(self, now: float) -> None:
        """Forget everything in progress (used after a pause)."""
        self._end_episode()
        self.owner_seen, self.away_armed = now, True

    @property
    def watched(self) -> float:
        # Up to the last frame an onlooker was actually seen: grace never counts.
        return 0.0 if self.episode_start is None else self.last_onlooker - self.episode_start

    def in_cooldown(self, now: float) -> bool:
        return now < self.cooldown_until

    def step(self, now: float, tracks, owner_enrolled: bool) -> list[Action]:
        actions: list[Action] = []
        if self.owner_seen is None:
            self.owner_seen = now
        if any(t.is_owner for t in tracks):
            self.owner_seen, self.away_armed = now, True
        onlooker = any(t.looking and not t.is_owner for t in tracks)
        cooling = self.in_cooldown(now)

        if cooling:
            self._end_episode()
        elif onlooker:
            if self.episode_start is None:
                self.episode_start = now
            self.last_onlooker = now
        elif self.episode_start is not None and now - self.last_onlooker > self.grace:
            self._end_episode()

        if self.episode_start is not None:
            w = self.watched
            if not self.warned and w >= self.warn_after:
                self.warned = True
                actions.append(Action(WARN, f"Someone is looking at your screen. Locking in "
                                            f"{max(0.0, self.lock_after - w):.0f}s."))
            if w >= self.lock_after:
                actions.append(Action(LOCK, f"Someone watched your screen for "
                                            f"{self.lock_after:.0f}s."))
                self._end_episode()
                self.cooldown_until = now + self.cooldown
                cooling = True

        if (self.away_lock > 0 and owner_enrolled and self.away_armed and not cooling
                and now - self.owner_seen >= self.away_lock):
            actions.append(Action(AWAY_LOCK, f"You were away for {self.away_lock:.0f}s."))
            self.away_armed = False  # re-armed when the owner is seen again
            self.cooldown_until = now + self.cooldown
        return actions
