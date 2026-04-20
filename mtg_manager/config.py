import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MoxfieldPackage:
    color_group: str
    public_id: str


PICK_LIST_SORT_OPTIONS = ("colour", "alphabetical", "set", "cmc")


@dataclass
class Config:
    packages: list[MoxfieldPackage]
    moxfield_delay: float
    mtgtop8_delay: float
    mtgtop8_cache_ttl: int
    db_path: Path
    pick_list_sort: str = "colour"
    formats: list[str] = field(default_factory=list)


DEFAULT_CONFIG = Path("~/.mtg_manager/config.toml").expanduser()


def get_git_commit() -> str:
    """Return the current git commit hash, or empty string if unavailable."""
    import subprocess
    repo_root = Path(__file__).parent.parent
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return ""


def load_config(path: Path | str | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Place your config.toml at: {DEFAULT_CONFIG}"
        )
    with open(path, "rb") as f:
        data = tomllib.load(f)

    packages = [
        MoxfieldPackage(color_group=p["color_group"], public_id=p["public_id"])
        for p in data["moxfield"]["packages"]
    ]

    output = data.get("output", {})
    pick_list_sort = output.get("pick_list_sort", "colour")
    if pick_list_sort not in PICK_LIST_SORT_OPTIONS:
        raise ValueError(
            f"Invalid pick_list_sort '{pick_list_sort}'. "
            f"Must be one of: {', '.join(PICK_LIST_SORT_OPTIONS)}"
        )

    return Config(
        packages=packages,
        moxfield_delay=data["moxfield"].get("request_delay_seconds", 1.0),
        mtgtop8_delay=data["mtgtop8"].get("request_delay_seconds", 1.5),
        mtgtop8_cache_ttl=data["mtgtop8"].get("cache_ttl_hours", 24),
        db_path=Path(data["database"]["path"]).expanduser(),
        pick_list_sort=pick_list_sort,
        formats=data.get("formats", {}).get("tracked", []),
    )
