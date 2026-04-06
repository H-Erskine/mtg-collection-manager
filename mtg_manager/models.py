from dataclasses import dataclass, field


@dataclass
class OwnedCard:
    name: str
    quantity: int
    color_group: str
    set_code: str = ""
    collector_number: str = ""
    foil: bool = False


@dataclass
class DeckCard:
    name: str
    quantity: int
    is_sideboard: bool = False


@dataclass
class Decklist:
    deck_id: str
    name: str
    url: str
    cards: list[DeckCard] = field(default_factory=list)

    @property
    def maindeck(self) -> list[DeckCard]:
        return [c for c in self.cards if not c.is_sideboard]

    @property
    def sideboard(self) -> list[DeckCard]:
        return [c for c in self.cards if c.is_sideboard]


@dataclass
class MissingCard:
    name: str
    needed: int        # max copies across all variants
    owned: int         # total owned (ignoring allocation)
    short: int         # needed - owned (always > 0) — needs ordering
    variants: int      # how many decks use this card
    total_variants: int


@dataclass
class BoxedCard:
    """Card you own enough of but some/all copies are allocated to a box."""
    name: str
    needed: int
    owned: int
    allocations: list  # list of BoxAllocation from db
