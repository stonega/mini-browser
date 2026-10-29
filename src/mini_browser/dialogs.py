"""Temporary native controls; none take space from the browsing surface."""

import threading
from pathlib import Path

from . import APP_ID, APP_NAME, VERSION
from .chrome import ChromeProfile, discover_profiles, import_html, import_profile
from .gtk import Adw, Gio, GLib, Gtk
from .navigation import address_to_uri


def padded_box(spacing=12):
    return Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=spacing,
        margin_start=20,
        margin_end=20,
        margin_top=12,
        margin_bottom=24,
    )


def dialog_shell(title, width=380, height=-1):
    dialog = Adw.Dialog(title=title, content_width=width, content_height=height)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    dialog.set_child(toolbar)
    return dialog, toolbar


def address_dialog(window):
    dialog, toolbar = dialog_shell("Open a website")
    box = padded_box()
    entry = Gtk.Entry(placeholder_text="Search or enter an address", activates_default=True)
    entry.set_text(window.webview.get_uri() or "")
    entry.select_region(0, -1)
    box.append(entry)
    error = Gtk.Label(wrap=True, xalign=0, visible=False)
    error.add_css_class("error")
    box.append(error)
    button = Gtk.Button(label="Open")
    button.add_css_class("suggested-action")

    def open_address(*_):
        try:
            uri = address_to_uri(entry.get_text())
        except ValueError as exc:
            error.set_text(str(exc))
            error.set_visible(True)
            return
        dialog.close()
        window.load_uri(uri)

    button.connect("clicked", open_address)
    entry.connect("activate", open_address)
    box.append(button)
    toolbar.set_content(box)
    dialog.set_default_widget(button)
    dialog.set_focus(entry)
    dialog.present(window)


def import_dialog(window):
    library = window.library
    dialog, toolbar = dialog_shell("Import from Chrome", height=640)
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
    box = padded_box(12)
    scroll.set_child(box)
    toolbar.set_content(scroll)
    description = Gtk.Label(
        label="Bring your bookmarks and browsing history into Mini Browser.",
        wrap=True,
        xalign=0,
    )
    box.append(description)

    profiles = discover_profiles()
    profile_names = Gtk.StringList.new([profile.label for profile in profiles])
    choices = Gtk.DropDown(model=profile_names, sensitive=bool(profiles), hexpand=True)
    choices.set_tooltip_text("Chrome profile")
    box.append(Gtk.Label(label="Profile", xalign=0, css_classes=["heading"]))
    box.append(choices)
    browse = Gtk.Button(label="Choose profile folder…")
    box.append(browse)
    checks = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
    bookmark_check = Adw.SwitchRow(title="Bookmarks", active=True)
    history_check = Adw.SwitchRow(title="Browsing history", active=True)
    checks.append(bookmark_check)
    checks.append(history_check)
    box.append(checks)
    status = Gtk.Label(
        label="Select a profile folder if Chrome is not listed." if not profiles else "",
        xalign=0,
        wrap=True,
    )
    status.add_css_class("dim-label")
    box.append(status)
    spinner = Gtk.Spinner(halign=Gtk.Align.CENTER, visible=False)
    box.append(spinner)
    submit = Gtk.Button(label="Import selected data", sensitive=bool(profiles))
    submit.add_css_class("suggested-action")
    box.append(submit)
    html_button = Gtk.Button(label="Import bookmarks from HTML…", css_classes=["flat"])
    box.append(html_button)
    box.append(
        Gtk.Label(
            label="Passwords use a separate Chrome CSV export. "
            "Cookies and signed-in sessions are not imported.",
            wrap=True,
            xalign=0,
            css_classes=["dim-label", "caption"],
        )
    )

    def update_submit(*_):
        submit.set_sensitive(
            bool(profiles) and (bookmark_check.get_active() or history_check.get_active())
        )

    bookmark_check.connect("notify::active", update_submit)
    history_check.connect("notify::active", update_submit)

    def choose_profile(*_):
        picker = Gtk.FileDialog(title="Choose a Chrome profile folder")

        def selected(picker, result):
            try:
                file = picker.select_folder_finish(result)
                path = file.get_path()
                if not path:
                    raise ValueError("Choose a local profile folder.")
                profile = ChromeProfile(Path(path), Path(path).name, "Selected folder")
                profiles.append(profile)
                profile_names.append(profile.label)
                choices.set_selected(len(profiles) - 1)
                choices.set_sensitive(True)
                status.set_label("Profile folder selected.")
                update_submit()
            except GLib.Error:
                pass  # The file picker was dismissed.
            except ValueError as exc:
                status.set_label(str(exc))

        picker.select_folder(window, None, selected)

    browse.connect("clicked", choose_profile)

    def run_import(work):
        # Read toggle state on the GTK thread, before the worker starts.
        app = window.get_application()
        for widget in [choices, browse, checks, submit, html_button, passwords]:
            widget.set_sensitive(False)
        dialog.set_can_close(False)
        spinner.set_visible(True)
        spinner.start()
        status.set_label("Importing…")

        def done(result, error):
            spinner.stop()
            spinner.set_visible(False)
            dialog.set_can_close(True)
            for widget in [browse, checks, html_button, passwords]:
                widget.set_sensitive(True)
            choices.set_sensitive(bool(profiles))
            update_submit()
            if error:
                status.set_label(error)
            else:
                message = (
                    f"Added {result.bookmarks:,} bookmarks and {result.history:,} history entries."
                )
                if result.warnings:
                    message += "\n" + "\n".join(result.warnings)
                status.set_label(message)
                submit.set_label("Import again")
                window.toast("Chrome data imported")
            return GLib.SOURCE_REMOVE

        def worker():
            try:
                result = work()
                GLib.idle_add(done, result, None)
            except Exception as exc:
                # Never include a traceback containing private source data in logs.
                message = str(exc) if isinstance(exc, (ValueError, OSError)) else "Import failed."
                GLib.idle_add(done, None, message)
            finally:
                GLib.idle_add(app.release)

        app.hold()
        threading.Thread(target=worker, name="chrome-import", daemon=True).start()

    def selected_import(*_):
        index = choices.get_selected()
        if index >= len(profiles):
            return
        path = profiles[index].path
        bookmarks = bookmark_check.get_active()
        history = history_check.get_active()
        run_import(lambda: import_profile(path, library, bookmarks=bookmarks, history=history))

    submit.connect("clicked", selected_import)

    def choose_html(*_):
        picker = Gtk.FileDialog(title="Choose Chrome bookmarks HTML")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        html_filter = Gtk.FileFilter(name="HTML bookmarks")
        html_filter.add_pattern("*.html")
        html_filter.add_pattern("*.htm")
        filters.append(html_filter)
        picker.set_filters(filters)

        def selected(picker, result):
            try:
                file = picker.open_finish(result)
                path = file.get_path()
                if not path:
                    status.set_label("Choose a local HTML file.")
                    return
                run_import(lambda: import_html(Path(path), library))
            except GLib.Error:
                pass

        picker.open(window, None, selected)

    html_button.connect("clicked", choose_html)
    passwords = Gtk.Button(label="Import passwords from Chrome CSV…", css_classes=["flat"])

    def choose_passwords(*_):
        from .password_dialogs import passwords_dialog

        dialog.force_close()
        passwords_dialog(window, import_now=True)

    passwords.connect("clicked", choose_passwords)
    box.append(passwords)
    dialog.present(window)
    return dialog


def library_dialog(window):
    dialog, toolbar = dialog_shell("Your library", height=580)
    box = padded_box()
    search = Gtk.SearchEntry(placeholder_text="Search your library")
    box.append(search)
    switcher = Gtk.StackSwitcher(halign=Gtk.Align.CENTER)
    stack = Gtk.Stack(vexpand=True)
    switcher.set_stack(stack)
    box.append(switcher)
    box.append(stack)
    lists = {}

    for kind, title in [("bookmarks", "Bookmarks"), ("history", "History")]:
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        rows.set_placeholder(
            Gtk.Label(
                label=f"No {kind} found.\nImport from Chrome to get started.",
                wrap=True,
                justify=Gtk.Justification.CENTER,
                margin_top=36,
                margin_bottom=36,
                css_classes=["dim-label"],
            )
        )
        scroll.set_child(rows)
        stack.add_titled(scroll, kind, title)
        lists[kind] = rows

    def refresh(*_):
        kind = stack.get_visible_child_name()
        rows = lists[kind]
        while rows.get_first_child():
            rows.remove(rows.get_first_child())
        for record in window.library.search(kind, search.get_text()):
            row = Adw.ActionRow(
                title=record["title"],
                subtitle=record["url"],
                title_lines=1,
                subtitle_lines=1,
                activatable=True,
                use_markup=False,
            )
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.set_tooltip_text(record["url"])

            def activate(_row, url=record["url"]):
                dialog.close()
                window.load_uri(url)

            row.connect("activated", activate)
            rows.append(row)

    search.connect("search-changed", refresh)
    stack.connect("notify::visible-child-name", refresh)
    toolbar.set_content(box)
    refresh()
    dialog.present(window)
    return dialog


def about_dialog(window):
    dialog = Adw.AboutDialog(
        application_name=APP_NAME,
        application_icon=APP_ID,
        version=VERSION,
        comments="A small, native GNOME browser. One page, one portrait window.",
        license_type=Gtk.License.GPL_3_0,
    )
    dialog.present(window)


def settings_dialog(window):
    dialog = Adw.PreferencesDialog(
        title="Settings",
        content_width=380,
        content_height=560,
        search_enabled=False,
    )
    page = Adw.PreferencesPage()
    for title, rows in [
        (
            "Import",
            [
                (
                    "Import bookmarks and history",
                    "Chrome profiles or bookmarks HTML",
                    "document-open-symbolic",
                    "win.import",
                ),
                (
                    "Import passwords",
                    "Chrome password CSV into your encrypted vault",
                    "dialog-password-symbolic",
                    "win.import-passwords",
                ),
            ],
        ),
        (
            "Browser",
            [
                (
                    "Devices",
                    "Show Android phones and iPhones as apps",
                    "phone-symbolic",
                    "win.devices",
                ),
                (
                    "Bookmarks and history",
                    "Browse your saved pages and visits",
                    "user-bookmarks-symbolic",
                    "win.library",
                ),
                (
                    "Passwords",
                    "Manage saved logins and your vault PIN",
                    "dialog-password-symbolic",
                    "win.passwords",
                ),
                (
                    "Site permissions",
                    "Camera, microphone, location and notifications",
                    "security-high-symbolic",
                    "win.permissions-all",
                ),
                (
                    "Keyboard shortcuts",
                    "Navigation and window controls",
                    "preferences-desktop-keyboard-shortcuts-symbolic",
                    "win.shortcuts",
                ),
            ],
        ),
    ]:
        group = Adw.PreferencesGroup(title=title)
        for label, subtitle, icon, action in rows:
            row = Adw.ActionRow(title=label, subtitle=subtitle, activatable=True, use_markup=False)
            row.add_prefix(Gtk.Image(icon_name=icon))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))

            def activate(_row, name=action):
                dialog.force_close()
                window.activate_action(name, None)

            row.connect("activated", activate)
            group.add(row)
        page.add(group)
    dialog.add(page)
    dialog.present(window)
    return dialog


def shortcuts_dialog(window):
    dialog, toolbar = dialog_shell("Keyboard shortcuts")
    box = padded_box()
    for title, accelerator in [
        ("Open a website or search", "<Primary>l"),
        ("Home page", "<Alt>Home"),
        ("Settings", "<Primary>comma"),
        ("Show media title bar", "F10"),
        ("New window", "<Primary>n"),
        ("Close window", "<Primary>w"),
        ("Reload", "<Primary>r"),
        ("Reload without cache", "<Primary><Shift>r"),
        ("Go back", "<Alt>Left"),
        ("Go forward", "<Alt>Right"),
        ("Bookmarks and history", "<Primary>b"),
        ("Bookmark this website as an app", "<Primary>d"),
        ("Import from Chrome", "<Primary><Shift>i"),
        ("Manage passwords", "<Primary><Shift>p"),
        ("Fill saved login", "<Primary><Shift>l"),
        ("Lock password vault", "<Primary><Shift>k"),
        ("Zoom in / out", "<Primary>plus"),
        ("Reset zoom", "<Primary>0"),
        ("Keyboard shortcuts", "<Primary>question"),
    ]:
        row = Gtk.Box(spacing=12)
        row.append(Gtk.Label(label=title, xalign=0, hexpand=True))
        row.append(Gtk.ShortcutLabel(accelerator=accelerator))
        box.append(row)
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=box)
    toolbar.set_content(scroll)
    dialog.present(window)
