"""Callbacks for the /profiles page — candle hover field management."""
from __future__ import annotations

import copy

from dash import Input, Output, State, callback, html, no_update, ALL, ctx

from layout_profiles import DEFAULT_PROFILES, FIELD_GROUPS


# ── Initialise dropdown from stored profiles ──────────────────────────────────

@callback(
    Output("profile-active-dd", "options"),
    Output("profile-active-dd", "value"),
    Input("hover-profile-store", "data"),
)
def sync_profile_dropdown(store_data: dict | None):
    data = store_data or DEFAULT_PROFILES
    names = list(data.get("profiles", {}).keys())
    options = [{"label": n, "value": n} for n in names]
    active = data.get("active", names[0] if names else "")
    return options, active


# ── Load selected fields into checklists when active profile changes ──────────

@callback(
    Output({"type": "profile-field-check", "group": ALL}, "value"),
    Input("profile-active-dd", "value"),
    State("hover-profile-store", "data"),
)
def load_profile_fields(active_name: str | None, store_data: dict | None):
    data = store_data or DEFAULT_PROFILES
    profiles = data.get("profiles", {})
    selected = set(profiles.get(active_name or "", {}).get("fields", []))
    return [
        [f for f in fields if f in selected]
        for _, fields in FIELD_GROUPS
    ]


# ── Compute selected count + preview ─────────────────────────────────────────

@callback(
    Output("profile-selected-count", "children"),
    Output("profile-hover-preview", "children"),
    Input({"type": "profile-field-check", "group": ALL}, "value"),
)
def update_preview(all_values):
    selected: list[str] = []
    for group_vals in (all_values or []):
        selected.extend(group_vals or [])

    count_msg = f"{len(selected)} field(s) selected"

    if not selected:
        preview = "No fields selected"
    else:
        # Simulated hover line
        preview_lines = [f"ts: 2026-05-23 14:32:00"]
        for f in selected[:20]:
            preview_lines.append(f"{f}: …")
        if len(selected) > 20:
            preview_lines.append(f"… +{len(selected) - 20} more")
        preview = "\n".join(preview_lines)

    return count_msg, preview


# ── Save current checklist selection into the active profile ──────────────────

@callback(
    Output("hover-profile-store", "data", allow_duplicate=True),
    Output("profile-status-div", "children"),
    Input("profile-save-btn", "n_clicks"),
    State("profile-active-dd", "value"),
    State({"type": "profile-field-check", "group": ALL}, "value"),
    State("hover-profile-store", "data"),
    prevent_initial_call=True,
)
def save_profile(n_clicks, active_name, all_values, store_data):
    if not n_clicks or not active_name:
        return no_update, no_update

    selected: list[str] = []
    for group_vals in (all_values or []):
        selected.extend(group_vals or [])

    data = copy.deepcopy(store_data or DEFAULT_PROFILES)
    if "profiles" not in data:
        data["profiles"] = {}
    if active_name not in data["profiles"]:
        data["profiles"][active_name] = {}
    data["profiles"][active_name]["fields"] = selected
    data["active"] = active_name

    return data, f"✓ Saved '{active_name}' ({len(selected)} fields)"


# ── Create a new profile ──────────────────────────────────────────────────────

@callback(
    Output("hover-profile-store", "data", allow_duplicate=True),
    Output("profile-active-dd", "options", allow_duplicate=True),
    Output("profile-active-dd", "value", allow_duplicate=True),
    Output("profile-new-name-inp", "value"),
    Output("profile-status-div", "children", allow_duplicate=True),
    Input("profile-create-btn", "n_clicks"),
    State("profile-new-name-inp", "value"),
    State("hover-profile-store", "data"),
    prevent_initial_call=True,
)
def create_profile(n_clicks, new_name, store_data):
    if not n_clicks:
        return no_update, no_update, no_update, no_update, no_update

    new_name = (new_name or "").strip()
    if not new_name:
        return no_update, no_update, no_update, no_update, "⚠ Enter a profile name"

    data = copy.deepcopy(store_data or DEFAULT_PROFILES)
    if "profiles" not in data:
        data["profiles"] = {}

    if new_name in data["profiles"]:
        return no_update, no_update, no_update, no_update, f"⚠ '{new_name}' already exists"

    data["profiles"][new_name] = {"fields": []}
    data["active"] = new_name

    names = list(data["profiles"].keys())
    options = [{"label": n, "value": n} for n in names]
    return data, options, new_name, "", f"✓ Created '{new_name}'"


# ── Delete the active profile ──────────────────────────────────────────────────

@callback(
    Output("hover-profile-store", "data", allow_duplicate=True),
    Output("profile-active-dd", "options", allow_duplicate=True),
    Output("profile-active-dd", "value", allow_duplicate=True),
    Output("profile-status-div", "children", allow_duplicate=True),
    Input("profile-delete-btn", "n_clicks"),
    State("profile-active-dd", "value"),
    State("hover-profile-store", "data"),
    prevent_initial_call=True,
)
def delete_profile(n_clicks, active_name, store_data):
    if not n_clicks or not active_name:
        return no_update, no_update, no_update, no_update

    data = copy.deepcopy(store_data or DEFAULT_PROFILES)
    profiles = data.get("profiles", {})

    if active_name not in profiles:
        return no_update, no_update, no_update, "⚠ Profile not found"

    # Cannot delete the last profile
    if len(profiles) <= 1:
        return no_update, no_update, no_update, "⚠ Cannot delete the last profile"

    del profiles[active_name]
    data["profiles"] = profiles
    new_active = next(iter(profiles))
    data["active"] = new_active

    names = list(profiles.keys())
    options = [{"label": n, "value": n} for n in names]
    return data, options, new_active, f"✓ Deleted '{active_name}'"


# ── Switch active profile when dropdown changes ───────────────────────────────

@callback(
    Output("hover-profile-store", "data", allow_duplicate=True),
    Input("profile-active-dd", "value"),
    State("hover-profile-store", "data"),
    prevent_initial_call=True,
)
def switch_active_profile(active_name, store_data):
    if not active_name:
        return no_update
    data = copy.deepcopy(store_data or DEFAULT_PROFILES)
    data["active"] = active_name
    return data
