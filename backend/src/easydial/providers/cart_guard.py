"""Account-scoped cart ownership for the single-operator development backend."""

from ..controller import DomainError


class CartGuard:
    def __init__(self):
        self.owner: str | None = None
        self.expected: tuple = ()
        self.attempted: tuple | None = None

    def check(self, session_id: str, actual: tuple):
        if not actual:
            self.owner, self.expected, self.attempted = session_id, (), None
            return
        if self.owner != session_id or actual not in (self.expected, self.attempted):
            raise DomainError(
                "This store has an existing or externally changed basket. Clear it in the store app first, "
                "then review again. EasyDial has not overwritten it."
            )

    def before_write(self, desired: tuple):
        self.attempted = desired

    def accepted(self, actual: tuple):
        self.expected, self.attempted = actual, None
