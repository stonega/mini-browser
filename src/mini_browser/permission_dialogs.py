"""Native site-permission management and request prompts."""

from . import permissions
from .gtk import Adw, Gtk

_STATES = (permissions.ASK, permissions.ALLOW, permissions.BLOCK)
_LABELS = ("Ask", "Allow", "Block")


def _permission_row(library, origin, name, window):
    label, subtitle = permissions.PERMISSIONS[name]
    row = Adw.ActionRow(title=label, subtitle=subtitle, use_markup=False)
    dropdown = Gtk.DropDown(
        model=Gtk.StringList.new(list(_LABELS)),
        valign=Gtk.Align.CENTER,
        tooltip_text=f"{label} permission for {permissions.origin_host(origin)}",
    )
    current = permissions.decision(library, origin, name)
    dropdown.set_selected(_STATES.index(current))
    restoring = False

    def save(dropdown, _pspec):
        nonlocal restoring
        if restoring:
            return
        state = _STATES[dropdown.get_selected()]
        try:
            library.set_permission(origin, name, state)
        except Exception:
            restoring = True
            dropdown.set_selected(_STATES.index(permissions.decision(library, origin, name)))
            restoring = False
            window.toast("Could not save this permission choice.")
            return
        window.apply_site_permissions(origin)

    dropdown.connect("notify::selected", save)
    row.add_suffix(dropdown)
    return row


def permissions_dialog(window, origin=None):
    """List saved choices for every site, or manage one site."""
    library = window.library
    if origin is not None:
        try:
            origin = permissions.normalize_origin(origin)
        except ValueError:
            origin = None
    dialog = Adw.PreferencesDialog(
        title="Site permissions" if origin else "Permissions",
        content_width=440,
        content_height=620,
        search_enabled=False,
    )
    page = Adw.PreferencesPage()
    dialog.add(page)
    groups = []

    def add(group):
        page.add(group)
        groups.append(group)

    def reset(site=None):
        try:
            library.clear_permissions(site)
        except Exception:
            window.toast("Could not reset site permissions.")
            return
        if site is None:
            window.reset_all_site_permissions()
        else:
            window.reset_site_permissions(site)
        window.toast("Site permissions reset")
        build()

    def build():
        for group in groups:
            page.remove(group)
        groups.clear()
        origins = (
            [origin]
            if origin
            else sorted({record["origin"] for record in library.permission_records()})
        )
        if not origins:
            group = Adw.PreferencesGroup()
            group.add(
                Adw.ActionRow(
                    title="No saved site permissions",
                    subtitle=(
                        "Sites ask before using the camera, microphone, location or notifications."
                    ),
                    use_markup=False,
                )
            )
            add(group)
            return
        for site in origins:
            group = Adw.PreferencesGroup(title=site)
            for name in permissions.PERMISSION_ORDER:
                group.add(_permission_row(library, site, name, window))
            if origin:
                row = Adw.ActionRow(
                    title="Reset this site",
                    subtitle="Websites will ask again",
                    activatable=True,
                    use_markup=False,
                )
                row.add_prefix(Gtk.Image(icon_name="edit-clear-symbolic"))
                row.connect("activated", lambda _row, site=site: reset(site))
                group.add(row)
            add(group)
        if not origin:
            group = Adw.PreferencesGroup()
            row = Adw.ActionRow(
                title="Reset all site permissions",
                subtitle="Every website will ask again",
                activatable=True,
                use_markup=False,
            )
            row.add_prefix(Gtk.Image(icon_name="edit-clear-all-symbolic"))
            row.connect("activated", lambda *_: reset())
            group.add(row)
            add(group)

    build()
    dialog.present(window)
    return dialog


def permission_prompt(window, request, callback):
    """Ask about one pending request; callback(allow, remember) follows."""
    host = permissions.origin_host(request.origin)
    names = getattr(request, "prompt_names", request.names)
    dialog = Adw.AlertDialog(
        heading="Permission request",
        body=(f"{host} wants to use your {permissions.describe_permissions(names)}."),
        body_use_markup=False,
    )
    remember = Gtk.CheckButton(label="Remember this choice for this site", active=True)
    remember.set_halign(Gtk.Align.START)
    dialog.set_extra_child(remember)
    dialog.add_response("dismiss", "Not now")
    dialog.add_response("block", "Block")
    dialog.add_response("allow", "Allow")
    dialog.set_response_appearance("allow", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("dismiss")
    dialog.set_close_response("dismiss")

    def response(_dialog, answer):
        callback(answer == "allow", answer != "dismiss" and remember.get_active())

    dialog.connect("response", response)
    dialog.present(window)
    return dialog
