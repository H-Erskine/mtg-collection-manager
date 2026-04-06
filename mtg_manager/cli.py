import sys
from collections import defaultdict

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .config import load_config
from .db import (
    card_count,
    clear_color_group,
    delete_built_deck,
    get_allocated_quantity,
    get_available_quantity,
    get_card_allocations,
    get_conn,
    get_deck,
    get_owned_quantity,
    insert_built_deck,
    list_built_decks,
    upsert_cards,
)
from .models import BoxedCard, MissingCard
from .moxfield import fetch_package_cards
from .mtgtop8 import fetch_decklists

console = Console()
err_console = Console(stderr=True)

DEFAULT_BOX = "white box"


def _load_cfg():
    try:
        return load_config()
    except FileNotFoundError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


def _auto_sync(cfg, conn) -> None:
    """Silently re-fetch all Moxfield packages. Only touches owned_cards, never box tables."""
    for pkg in cfg.packages:
        try:
            cards = fetch_package_cards(pkg, delay=cfg.moxfield_delay)
            clear_color_group(conn, pkg.color_group)
            upsert_cards(conn, cards)
        except Exception as e:
            err_console.print(f"[yellow]Warning: sync failed for {pkg.color_group}: {e}[/yellow]")


@click.group()
def cli():
    """MTG collection manager — sync from Moxfield, check missing cards for MTGTop8 decklists."""


# ---------------------------------------------------------------------------
# mtg sync
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--color-group", "-c", default=None, help="Only sync a specific color group.")
def sync(color_group):
    """Fetch Moxfield packages and update the local collection database."""
    cfg = _load_cfg()

    packages = cfg.packages
    if color_group:
        packages = [p for p in packages if p.color_group.lower() == color_group.lower()]
        if not packages:
            err_console.print(f"[red]No package found for color group '{color_group}'.[/red]")
            sys.exit(1)

    with get_conn(cfg.db_path) as conn:
        for pkg in packages:
            with Progress(
                SpinnerColumn(),
                TextColumn(f"[cyan]Syncing {pkg.color_group}...[/cyan]"),
                console=console,
                transient=True,
            ) as progress:
                progress.add_task("sync")
                try:
                    cards = fetch_package_cards(pkg, delay=cfg.moxfield_delay)
                except Exception as e:
                    err_console.print(f"[red]Failed to fetch {pkg.color_group} ({pkg.public_id}): {e}[/red]")
                    continue

            clear_color_group(conn, pkg.color_group)
            upsert_cards(conn, cards)
            qty = sum(c.quantity for c in cards)
            console.print(f"  [green]OK[/green] {pkg.color_group}: {qty} cards ({len(cards)} unique entries)")

        console.print(f"\n[bold]Collection total:[/bold] {card_count(conn)} cards")


# ---------------------------------------------------------------------------
# mtg missing
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("url")
@click.option("--sideboard/--no-sideboard", default=False, show_default=True,
              help="Include sideboard cards.")
@click.option("--min-variants", "-m", default=1, show_default=True, type=int,
              help="Only show cards appearing in at least N variants.")
def missing(url, sideboard, min_variants):
    """Show cards you need to order for a deck or compare URL."""
    cfg = _load_cfg()

    with Progress(SpinnerColumn(), TextColumn("[cyan]Fetching decklists...[/cyan]"),
                  console=console, transient=True) as progress:
        progress.add_task("fetch")
        try:
            decklists = fetch_decklists(url, delay=cfg.mtgtop8_delay)
        except Exception as e:
            err_console.print(f"[red]Failed to fetch decklists: {e}[/red]")
            sys.exit(1)

    if not decklists:
        err_console.print("[red]No decklists found at that URL.[/red]")
        sys.exit(1)

    console.print(f"\nFound [bold]{len(decklists)}[/bold] deck variant(s):\n")
    for dl in decklists:
        console.print(f"  * {dl.name}  [dim]({dl.deck_id})[/dim]")
    console.print()

    max_needed: dict[str, int] = defaultdict(int)
    variant_count: dict[str, int] = defaultdict(int)
    canonical_name: dict[str, str] = {}

    for dl in decklists:
        cards = dl.cards if sideboard else dl.maindeck
        for card in cards:
            key = card.name.lower()
            canonical_name[key] = card.name
            max_needed[key] = max(max_needed[key], card.quantity)
            variant_count[key] += 1

    keys = [k for k in max_needed if variant_count[k] >= min_variants]

    with get_conn(cfg.db_path) as conn:
        _auto_sync(cfg, conn)
        missing_cards: list[MissingCard] = []   # need to order
        boxed_cards: list[BoxedCard] = []        # owned but locked in a box

        for key in keys:
            name = canonical_name[key]
            needed = max_needed[key]
            owned = get_owned_quantity(conn, name)
            allocs = get_card_allocations(conn, name)
            allocated = sum(a.quantity for a in allocs)
            available = owned - allocated

            if owned < needed:
                # Genuinely don't own enough — need to order
                missing_cards.append(
                    MissingCard(
                        name=name,
                        needed=needed,
                        owned=owned,
                        short=needed - owned,
                        variants=variant_count[key],
                        total_variants=len(decklists),
                    )
                )
            elif available < needed and allocs:
                # Own enough total but copies are sitting in a box
                boxed_cards.append(BoxedCard(
                    name=name,
                    needed=needed,
                    owned=owned,
                    allocations=allocs,
                ))

    # --- Output ---
    if not missing_cards and not boxed_cards:
        console.print("[green]You have all the cards and they are available![/green]")
        return

    if not missing_cards and boxed_cards:
        console.print("[green]You own all the cards![/green] "
                      "[yellow]But some are currently in boxes:[/yellow]\n")
        for bc in sorted(boxed_cards, key=lambda c: c.name):
            for a in bc.allocations:
                console.print(
                    f"  {bc.needed}x {bc.name}  ->  "
                    f"{a.quantity}x in [{a.box_name}] ({a.deck_name})"
                )
        return

    # There are cards to order
    missing_cards.sort(key=lambda c: (-c.variants, c.name))
    console.print(f"[bold red]Missing {len(missing_cards)} card(s) to order:[/bold red]\n")

    show_variants = len(decklists) > 1
    if show_variants:
        table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        table.add_column("Qty", justify="right", style="bold yellow", width=4)
        table.add_column("Card", min_width=30)
        table.add_column("Owned", justify="right", width=6)
        table.add_column(f"Variants/{len(decklists)}", justify="right", width=10)
        for c in missing_cards:
            table.add_row(str(c.short), c.name, str(c.owned), f"{c.variants}/{c.total_variants}")
        console.print(table)
    else:
        for c in missing_cards:
            console.print(f"{c.short}x {c.name}")

    if boxed_cards:
        console.print("\n[yellow]Also in boxes (owned but allocated):[/yellow]")
        for bc in sorted(boxed_cards, key=lambda c: c.name):
            for a in bc.allocations:
                console.print(
                    f"  {bc.needed}x {bc.name}  ->  "
                    f"{a.quantity}x in [{a.box_name}] ({a.deck_name})"
                )

    console.print()
    console.print("[bold]Order list:[/bold]")
    for c in missing_cards:
        console.print(f"{c.short}x {c.name}")


# ---------------------------------------------------------------------------
# mtg build
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("url")
@click.option("--box", "-b", default=DEFAULT_BOX, show_default=True,
              help="Name of the deck box to store this deck in.")
@click.option("--sideboard/--no-sideboard", default=False, show_default=True,
              help="Include sideboard cards in the allocation.")
def build(url, box, sideboard):
    """
    Mark a deck as built and allocate its cards to a box.

    Checks available copies (owned minus already-boxed cards).
    Warns if any cards are already in other boxes.
    """
    cfg = _load_cfg()

    with Progress(SpinnerColumn(), TextColumn("[cyan]Fetching decklist...[/cyan]"),
                  console=console, transient=True) as progress:
        progress.add_task("fetch")
        try:
            decklists = fetch_decklists(url, delay=cfg.mtgtop8_delay)
        except Exception as e:
            err_console.print(f"[red]Failed to fetch decklist: {e}[/red]")
            sys.exit(1)

    if not decklists:
        err_console.print("[red]No decklist found at that URL.[/red]")
        sys.exit(1)

    if len(decklists) > 1:
        err_console.print(
            "[red]Compare URLs contain multiple decks — use a single deck URL for build.[/red]\n"
            "Tip: click through to a specific deck on MTGTop8 and use that URL."
        )
        sys.exit(1)

    dl = decklists[0]

    with get_conn(cfg.db_path) as conn:
        _auto_sync(cfg, conn)
        # Check if already built
        existing = get_deck(conn, dl.deck_id)
        if existing:
            err_console.print(
                f"[yellow]Deck '{dl.name}' ({dl.deck_id}) is already built "
                f"and in [{existing['box_name']}].[/yellow]\n"
                f"Run [bold]mtg unbox {dl.deck_id}[/bold] first to rebuild it."
            )
            sys.exit(1)

        cards = dl.cards if sideboard else dl.maindeck
        # Aggregate quantities
        needed: dict[str, int] = defaultdict(int)
        for card in cards:
            needed[card.name] += card.quantity

        # Check availability and collect conflicts
        short_cards: list[tuple[str, int, int]] = []   # (name, needed, available)
        conflicts: dict[str, list[str]] = {}

        for name, qty in sorted(needed.items()):
            available = get_available_quantity(conn, name)
            allocs = get_card_allocations(conn, name)
            if allocs:
                conflicts[name] = [f"{a.quantity}x in [{a.box_name}] ({a.deck_name})" for a in allocs]
            if available < qty:
                short_cards.append((name, qty, available))

        if conflicts:
            console.print("[yellow]Warning — cards already allocated to other boxes:[/yellow]")
            for name, warns in sorted(conflicts.items()):
                for w in warns:
                    console.print(f"  {name}: {w}")
            console.print()

        if short_cards:
            console.print(f"[red]Cannot build — {len(short_cards)} card(s) unavailable:[/red]\n")
            for name, qty, available in short_cards:
                console.print(f"  need {qty}x {name}, have {available} available")
            sys.exit(1)

        # All good — record the build
        insert_built_deck(
            conn,
            deck_id=dl.deck_id,
            deck_name=dl.name,
            deck_url=url,
            box_name=box,
            cards=list(needed.items()),
        )

    console.print(f"[green]Built:[/green] {dl.name}")
    console.print(f"[green]Box:[/green]   {box}")
    console.print(f"[dim]Deck ID: {dl.deck_id}[/dim]")


# ---------------------------------------------------------------------------
# mtg boxes
# ---------------------------------------------------------------------------

@cli.command()
def boxes():
    """List all deck boxes and the decks inside them."""
    cfg = _load_cfg()

    with get_conn(cfg.db_path) as conn:
        _auto_sync(cfg, conn)
        decks = list_built_decks(conn)

    if not decks:
        console.print("No decks built yet. Use [bold]mtg build <url>[/bold] to add one.")
        return

    current_box = None
    for row in decks:
        if row["box_name"] != current_box:
            current_box = row["box_name"]
            console.print(f"\n[bold cyan][{current_box}][/bold cyan]")
        console.print(f"  {row['deck_name']}  [dim]({row['deck_id']})[/dim]")
    console.print()


# ---------------------------------------------------------------------------
# mtg unbox
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("deck_id")
def unbox(deck_id):
    """Remove a built deck from its box and return its cards to the available pool."""
    cfg = _load_cfg()

    with get_conn(cfg.db_path) as conn:
        row = get_deck(conn, deck_id)
        if not row:
            err_console.print(f"[red]No built deck found with ID '{deck_id}'.[/red]")
            err_console.print("Run [bold]mtg boxes[/bold] to see built decks.")
            sys.exit(1)

        name = row["deck_name"]
        box = row["box_name"]
        delete_built_deck(conn, deck_id)

    console.print(f"[green]Unboxed:[/green] {name} (was in [{box}])")
    console.print("Cards returned to available pool.")
