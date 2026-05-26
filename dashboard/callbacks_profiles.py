"""Callbacks for the /profiles page -- candle hover field management."""
from __future__ import annotations

import copy
import json

from dash import Input, Output, State, callback, html, no_update, ALL

import profile_storage
from layout_profiles import DEFAULT_PROFILES, FIELD_GROUPS


# ── On page open: show file info ──────────────────────────────────────────────

@callback(
    Output("profile-file-info", "children"),
    Input("url", "pathname"),
)
def show_file_info(pathname):
    """Update file info div whenever the URL changes."""
    path = profile_storage.file_path()
    if profile_storage.exists():
        return html.Span(f"profiles.json: {path}", style={"color": "#4fc3f7"})
    return html.Span(f"profiles.json not yet saved  ({path})", style={"color": "#888"})


# ── Auto-load from file when store is empty and user visits /profiles ─────────

@callback(
    Output("hover-profile-store", "data", allow_duplicate=True),
    Input("url", "pathname"),
    State("hover-profile-store", "data"),
    prevent_initial_call=True,
)
def auto_load_on_profiles_visit(pathname, store_data):
    """Auto-populate store from file the first time (when localStorage is empty)."""
    if pathname != "/profiles":
        return no_update
    if store_data is not None:
        return no_update
    from_file = profile_storage.load()
    return from_file if from_file else no_update


# ── Initialise dropdown from stored profiles ──────────────────────────────────

@callback(
    Output("profile-active-dd", "options"),
    Output("profile-active-dd", "value"),
    Input("hover-profile-store", "data"),
)
def sync_profile_dropdown(store_data):
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
def load_profile_fields(active_name, store_data):
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
        lines = ["ts: 2026-05-23 14:32:00"]
        for f in selected[:20]:
            lines.append(f"{f}: ...")
        if len(selected) > 20:
            lines.append(f"... +{len(selected) - 20} more")
        preview = "\n".join(lines)

    return count_msg, preview


# ── Save current checklist into active profile (browser store) ────────────────

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

    return data, f"Saved '{active_name}' ({len(selected)} fields)"


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
        return no_update, no_update, no_update, no_update, "Enter a profile name"

    data = copy.deepcopy(store_data or DEFAULT_PROFILES)
    if "profiles" not in data:
        data["profiles"] = {}

    if new_name in data["profiles"]:
        return no_update, no_update, no_update, no_update, f"'{new_name}' already exists"

    data["profiles"][new_name] = {"fields": []}
    data["active"] = new_name

    names = list(data["profiles"].keys())
    options = [{"label": n, "value": n} for n in names]
    return data, options, new_name, "", f"Created '{new_name}'"


# ── Delete the active profile ─────────────────────────────────────────────────

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
        return no_update, no_update, no_update, "Profile not found"
    if len(profiles) <= 1:
        return no_update, no_update, no_update, "Cannot delete the last profile"

    del profiles[active_name]
    data["profiles"] = profiles
    new_active = next(iter(profiles))
    data["active"] = new_active

    names = list(profiles.keys())
    options = [{"label": n, "value": n} for n in names]
    return data, options, new_active, f"Deleted '{active_name}'"


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


# ── Save to server file ───────────────────────────────────────────────────────

@callback(
    Output("profile-status-div", "children", allow_duplicate=True),
    Output("profile-file-info", "children", allow_duplicate=True),
    Input("profile-save-file-btn", "n_clicks"),
    State("hover-profile-store", "data"),
    prevent_initial_call=True,
)
def save_to_file(n_clicks, store_data):
    if not n_clicks:
        return no_update, no_update

    data = store_data or DEFAULT_PROFILES
    ok = profile_storage.save(data)
    path = profile_storage.file_path()

    if ok:
        n = len(data.get("profiles", {}))
        return (
            f"Saved {n} profile(s) to file",
            html.Span(f"profiles.json: {path}", style={"color": "#4fc3f7"}),
        )
    return (
        "Error: could not write file (check logs)",
        html.Span(f"profiles.json: {path}  (write failed)", style={"color": "#f44336"}),
    )


# ── Load from server file ─────────────────────────────────────────────────────

@callback(
    Output("hover-profile-store", "data", allow_duplicate=True),
    Output("profile-status-div", "children", allow_duplicate=True),
    Output("profile-file-info", "children", allow_duplicate=True),
    Input("profile-load-file-btn", "n_clicks"),
    prevent_initial_call=True,
)
def load_from_file(n_clicks):
    if not n_clicks:
        return no_update, no_update, no_update

    path = profile_storage.file_path()

    if not profile_storage.exists():
        return (
            no_update,
            "File not found -- save first",
            html.Span(f"profiles.json: {path}  (not found)", style={"color": "#f44336"}),
        )

    data = profile_storage.load()
    if data is None:
        return (
            no_update,
            "Error reading file (check logs)",
            html.Span(f"profiles.json: {path}  (read error)", style={"color": "#f44336"}),
        )

    n = len(data.get("profiles", {}))
    return (
        data,
        f"Loaded {n} profile(s) from file",
        html.Span(f"profiles.json: {path}", style={"color": "#4fc3f7"}),
    )


# ── Export JSON to browser download ──────────────────────────────────────────

@callback(
    Output("profile-download", "data"),
    Input("profile-export-btn", "n_clicks"),
    State("hover-profile-store", "data"),
    prevent_initial_call=True,
)
def export_json(n_clicks, store_data):
    if not n_clicks:
        return no_update

    data = store_data or DEFAULT_PROFILES
    content = json.dumps(data, indent=2, ensure_ascii=False)
    return {"content": content, "filename": "profiles.json", "type": "application/json"}
