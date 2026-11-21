"""Account-scoped cart ownership for the single-operator development backend."""

from ..controller import DomainError


class CartConflict(DomainError):
    def __init__(self):
        super().__init__(
            "The store cart differs from EasyDial's saved checkpoint. Review the differences before updating it."
        )
        self.cart = None
        self.previous_lines = []


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
            raise CartConflict()

    def check_clear(self, session_id: str, actual: tuple):
        """Explicit clearing may resume after some owned items were removed.

        Never adopt new products, increased quantities, or another draft's cart.
        A failed deletion must not prevent clearing the remaining owned rows.
        """
        if not actual:
            return self.check(session_id, actual)
        expected = {(name, pack): quantity for name, pack, quantity in self.expected}
        if self.owner != session_id or any(
            (name, pack) not in expected or quantity > expected[name, pack]
            for name, pack, quantity in actual
        ):
            raise CartConflict()

    def before_write(self, desired: tuple):
        self.attempted = desired

    def accepted(self, actual: tuple):
        self.expected, self.attempted = actual, None
