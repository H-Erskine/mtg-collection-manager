import sys
from collections import defaultdict

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .config import get_git_commit, load_config
from .db import (
    card_count,
    categorise_missing_cards,
    clear_color_group,
    delete_built_deck,
    get_allocated_quantity,
    get_available_quantity,
    get_card_allocations,
    get_cards_over_limit,
    get_conn,
    get_deck,
    get_owned_quantity,
    insert_built_deck,
    list_built_decks,
    upsert_cards,
)
from .models import BoxedCard, MissingCard
from .moxfield import fetch_package_cards
from .sources import fetch_decklists

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


def _boxed_lines(boxed_cards: list[BoxedCard]) -> list[str]:
    """Return formatted lines for cards owned but locked in boxes."""
    lines = []
    for bc in sorted(boxed_cards, key=lambda c: c.name):
        for a in bc.allocations:
            lines.append(f"  {bc.needed}x {bc.name}  ->  {a.quantity}x in [{a.box_name}] ({a.deck_name})")
    return lines


@click.group()
def cli():
    """MTG collection manager — sync from Moxfield, check missing cards for MTGTop8 decklists."""


# ---------------------------------------------------------------------------
# mtg version
# ---------------------------------------------------------------------------

@cli.command()
def version():
    """Show the current git commit this installation is running."""
    commit = get_git_commit()
    if not commit:
        console.print("[yellow]Could not determine version (git unavailable).[/yellow]")
        return
    console.print(f"[bold]mtg-manager[/bold]  commit [cyan]{commit[:7]}[/cyan]  [dim]({commit})[/dim]")


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
@click.argument("urls", nargs=-1, required=True)
@click.option("--sideboard/--no-sideboard", default=False, show_default=True,
              help="Include sideboard cards.")
@click.option("--min-variants", "-m", default=1, show_default=True, type=int,
              help="Only show cards appearing in at least N variants (single-URL compare mode).")
def missing(urls, sideboard, min_variants):
    """Show cards you need to order for one or more deck URLs.

    Pass multiple URLs to compare across lists — any deck you can build
    right now will be highlighted, and aggregate missing cards are shown.
    """
    cfg = _load_cfg()

    all_decklists = []
    with Progress(SpinnerColumn(), TextColumn("[cyan]Fetching decklists...[/cyan]"),
                  console=console, transient=True) as progress:
        progress.add_task("fetch")
        for url in urls:
            try:
                dls = fetch_decklists(url, delay=cfg.mtgtop8_delay)
                all_decklists.extend(dls)
            except Exception as e:
                err_console.print(f"[red]Failed to fetch {url}: {e}[/red]")

    if not all_decklists:
        err_console.print("[red]No decklists found.[/red]")
        sys.exit(1)

    multi_url = len(urls) > 1

    console.print(f"\nFound [bold]{len(all_decklists)}[/bold] deck(s):\n")
    for dl in all_decklists:
        console.print(f"  * {dl.name}  [dim]({dl.deck_id})[/dim]")
    console.print()

    # -----------------------------------------------------------------
    # Single-URL path: existing variant-compare behaviour
    # -----------------------------------------------------------------
    if not multi_url:
        decklists = all_decklists
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
            card_needs = [(canonical_name[k], max_needed[k], variant_count[k]) for k in keys]
            missing_cards, boxed_cards = categorise_missing_cards(conn, card_needs, len(decklists))

        if not missing_cards and not boxed_cards:
            console.print("[green]You have all the cards and they are available![/green]")
            return

        if not missing_cards and boxed_cards:
            console.print("[green]You own all the cards![/green] "
                          "[yellow]But some are currently in boxes:[/yellow]\n")
            for line in _boxed_lines(boxed_cards):
                console.print(line)
            return

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
            for line in _boxed_lines(boxed_cards):
                console.print(line)

        console.print()
        console.print("[bold]Order list:[/bold]")
        for c in missing_cards:
            console.print(f"{c.short}x {c.name}")
        return

    # -----------------------------------------------------------------
    # Multi-URL path: per-deck analysis + aggregate
    # -----------------------------------------------------------------
    # deck_result: (decklist, missing_cards, boxed_cards, needed_map)
    DeckResult = tuple  # (dl, list[MissingCard], list[BoxedCard], dict[str,int])

    with get_conn(cfg.db_path) as conn:
        _auto_sync(cfg, conn)

        deck_results: list[DeckResult] = []
        for dl in all_decklists:
            cards = dl.cards if sideboard else dl.maindeck
            # canonical name map for this deck: lower_name -> (canonical_name, quantity)
            needed_map: dict[str, tuple[str, int]] = {}
            for card in cards:
                key = card.name.lower()
                if key not in needed_map or card.quantity > needed_map[key][1]:
                    needed_map[key] = (card.name, card.quantity)

            total_slots = sum(qty for _, qty in needed_map.values())
            card_needs = [(name, qty, 1) for _, (name, qty) in needed_map.items()]
            dm, db = categorise_missing_cards(conn, card_needs, 1)
            have_slots = sum(
                min(get_owned_quantity(conn, name), qty) for _, (name, qty) in needed_map.items()
            )

            deck_results.append((dl, dm, db, needed_map, have_slots, total_slots))

    # --- Summary table ---
    total_decks = len(deck_results)
    summary = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    summary.add_column("Deck", min_width=35)
    summary.add_column("Have", justify="right", width=9)
    summary.add_column("Missing", justify="right", width=8)
    summary.add_column("In boxes", justify="right", width=9)
    summary.add_column("Status", width=14)

    buildable: list[DeckResult] = []
    for dl, dm, db, nm, have, total in deck_results:
        if not dm:
            status = "[green]Buildable![/green]" if not db else "[yellow]In boxes[/yellow]"
            buildable.append((dl, dm, db, nm, have, total))
        else:
            status = ""
        have_style = "green" if have == total else "yellow" if have >= total * 0.9 else "red"
        summary.add_row(
            dl.name,
            f"[{have_style}]{have}/{total}[/{have_style}]",
            str(len(dm)),
            str(len(db)),
            status,
        )

    console.print(summary)
    console.print()

    # --- Highlight buildable decks ---
    if buildable:
        from rich.panel import Panel
        for dl, dm, db, nm, have, total in buildable:
            cards = dl.cards if sideboard else dl.maindeck
            card_lines = sorted(
                {f"{card.quantity}x {card.name}" for card in cards}
            )
            body = "\n".join(card_lines)
            if db:
                body += "\n\n[yellow]Cards in boxes (owned but allocated):[/yellow]"
                body += "\n" + "\n".join(_boxed_lines(db))
            console.print(Panel(
                body,
                title=f"[bold green] You can build: {dl.name} [/bold green]",
                border_style="green",
            ))
        console.print()

    # --- Aggregate missing across all decks ---
    # For each card: total shortfall, number of decks needing it
    agg_short: dict[str, int] = defaultdict(int)   # sum of shortfalls
    agg_decks: dict[str, int] = defaultdict(int)   # how many decks need it
    canonical: dict[str, str] = {}

    for dl, dm, db, nm, have, total in deck_results:
        for mc in dm:
            key = mc.name.lower()
            canonical[key] = mc.name
            agg_short[key] += mc.short
            agg_decks[key] += 1

    if not agg_short:
        console.print("[green]You can build all the decks![/green]")
        return

    agg_list = sorted(
        agg_short.keys(),
        key=lambda k: (-agg_decks[k], -agg_short[k], k),
    )

    console.print(f"[bold red]Aggregate missing cards across {total_decks} decks:[/bold red]\n")
    agg_table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    agg_table.add_column("Avg short", justify="right", style="bold yellow", width=10)
    agg_table.add_column("Card", min_width=30)
    agg_table.add_column(f"Decks/{total_decks}", justify="right", width=10)

    for key in agg_list:
        name = canonical[key]
        avg = round(agg_short[key] / agg_decks[key], 1)
        avg_str = str(int(avg)) if avg == int(avg) else str(avg)
        agg_table.add_row(avg_str, name, f"{agg_decks[key]}/{total_decks}")

    console.print(agg_table)
    console.print()
    console.print("[bold]Order list (worst case — to cover all decks):[/bold]")
    for key in agg_list:
        console.print(f"{agg_short[key]}x {canonical[key]}")


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
# mtg extras
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--limit", "-l", default=4, show_default=True, type=int,
              help="Flag cards with more than this many copies.")
def extras(limit):
    """List cards you own more than 4 copies of (potential trade/sell stock)."""
    cfg = _load_cfg()

    with get_conn(cfg.db_path) as conn:
        _auto_sync(cfg, conn)
        rows = get_cards_over_limit(conn, limit)

    if not rows:
        console.print(f"No cards with more than {limit} copies.")
        return

    console.print(f"\n[bold]Cards with more than {limit} copies:[/bold]\n")
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Copies", justify="right", style="bold yellow", width=7)
    table.add_column("Card", min_width=35)
    table.add_column("Package", style="dim")

    for row in rows:
        table.add_row(str(row["quantity"]), row["name"], row["color_group"])

    console.print(table)
    console.print(f"\n[dim]{len(rows)} card(s) total[/dim]")


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
