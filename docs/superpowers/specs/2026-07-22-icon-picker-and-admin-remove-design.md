# Icon Picker & Admin User Removal — Design Spec
_2026-07-22_

## Scope

Two small, independent additions to the self-service config/admin experience:

1. **Icon picker**: replace the free-text icon `<input>` on `/config`'s Profile section with a fixed palette of clickable emoji buttons.
2. **Admin user removal**: the Whitelist (Admin) section on `/config` gains a "Remove" button per email, which fully deletes that person's account — whitelist entry, registry row (packages/formats/sort cascade via the existing foreign key), and their per-user collection database file. Irreversible.

## 1. Icon picker

No backend change — `POST /api/config/profile` already just stores whatever string is sent as `icon`; a fixed palette is a frontend-only constraint.

`webapp/static/config.html`: replace the icon `<input>` with a row of clickable buttons for a fixed set of options:

```
⚔ 🐉 🔥 💧 🌿 ☀️ 💀 🌈 🃏 🎲 🦄 🧙 👑 ⭐
```

Clicking one sets it as the selected icon (visually highlighted, same active-state pattern as the existing colour/person tiles) and stores the choice in a hidden field / JS variable that `saveProfile()` sends. Loading `/config` highlights whichever icon (if any) matches the account's current saved value; if the saved value isn't in the fixed list (e.g. a stray value from before this change), no option is highlighted but the save still works with the current selection once one is picked.

## 2. Admin user removal

### New registry function (`api/users.py`)

```python
def remove_whitelisted_user(email: str) -> None:
    """Fully delete a whitelisted user's account: whitelist entry, registry
    row (packages/formats/sort cascade via FK), and their per-user DB file.
    Irreversible. Refuses to remove the owner's own email."""
```

Guards against removing `OWNER_GOOGLE_EMAIL` (the owner can't lock themselves out via this route — matches the existing pattern of protecting the owner identity elsewhere in this codebase).

Deletes, in order:
1. The `google:<email>` row from `users` (cascades to `user_packages` via the existing `ON DELETE CASCADE` foreign key).
2. The `email` row from `whitelisted_emails`.
3. The per-user SQLite file at `_USERS_DIR / f"{_safe_filename(f'google:{email}')}.sqlite"`, if it exists on disk.

### New route: `DELETE /api/admin/whitelist/{email}`

Gated by `require_admin` (existing). Calls `remove_whitelisted_user(email)`. Returns 400 if the target email is the owner's own email (server-side guard, not just a UI one).

### Frontend (`webapp/static/config.html`)

Each row in the Whitelist (Admin) list gains a "Remove" button (styled like the existing package-removal buttons). Clicking it shows a native `confirm()` dialog ("Permanently delete <email>'s account and collection? This cannot be undone.") before calling the delete endpoint — this is irreversible real-data deletion, so a confirmation step is required, not optional.

## Out of scope

- Removing a Discord-identity user (the whitelist only ever contains emails for Google-login gating; Discord users aren't whitelist-gated at all in this system, so there's no equivalent "remove" surface for them here).
- Any UI to demote an existing admin back to non-admin (add-only for `is_admin` remains as originally scoped — this spec only adds full removal, not editing).
