"""Native master-password, import, management and login selection dialogs."""

import json
import threading
from pathlib import Path

from . import autofill
from .dialogs import dialog_shell, padded_box
from .gtk import Adw, Gio, GLib, Gtk
from .vault import VaultError, can_fill_origin, credential_origin


def vault_shell(title, height=-1):
    dialog, toolbar = dialog_shell(title, height=height)
    dialog._vault_closed = False
    dialog._password_dialog = True

    def closed(dialog):
        dialog._vault_closed = True

        def clear(widget):
            if isinstance(widget, Gtk.PasswordEntry):
                widget.set_text("")
            child = widget.get_first_child()
            while child:
                clear(child)
                child = child.get_next_sibling()

        clear(dialog)

    dialog.connect("closed", closed)
    return dialog, toolbar


def run_task(window, dialog, status, controls, work, completed):
    app = window.get_application()
    dialog.set_can_close(False)
    for control in controls:
        control.set_sensitive(False)
    status.set_label("Working…")

    def finish(result, error):
        try:
            if dialog._vault_closed or window._closing:
                app.lock_vault()
                return GLib.SOURCE_REMOVE
            dialog.set_can_close(True)
            for control in controls:
                control.set_sensitive(True)
            if error:
                status.set_label(error)
            else:
                completed(result)
        finally:
            app.release()
        return GLib.SOURCE_REMOVE

    def worker():
        try:
            result = work()
        except Exception as exc:
            message = (
                str(exc) if isinstance(exc, VaultError) else "Could not update the password vault."
            )
            GLib.idle_add(finish, None, message)
        else:
            GLib.idle_add(finish, result, None)

    app.hold()
    threading.Thread(target=worker, name="password-vault", daemon=True).start()


def ensure_unlocked(window, completed):
    vault = window.get_application().vault
    if vault.unlocked:
        completed()
        return
    creating = not vault.exists
    dialog, toolbar = vault_shell("Create password vault" if creating else "Unlock password vault")
    box = padded_box()
    box.append(
        Gtk.Label(
            label="Your passwords are encrypted on this device. "
            "Only your master password can unlock them.",
            wrap=True,
            xalign=0,
        )
    )
    password = Gtk.PasswordEntry(show_peek_icon=True, activates_default=True)
    password.set_tooltip_text("Master password")
    box.append(Gtk.Label(label="Master password", xalign=0))
    box.append(password)
    confirm = Gtk.PasswordEntry(show_peek_icon=True, activates_default=True)
    if creating:
        box.append(Gtk.Label(label="Confirm master password", xalign=0))
        box.append(confirm)
        box.append(
            Gtk.Label(
                label="Use at least 12 characters. If you forget this password, "
                "the vault cannot be recovered.",
                wrap=True,
                xalign=0,
                css_classes=["dim-label", "caption"],
            )
        )
    status = Gtk.Label(wrap=True, xalign=0)
    box.append(status)
    submit = Gtk.Button(
        label="Create vault" if creating else "Unlock",
        css_classes=["suggested-action"],
    )
    box.append(submit)

    def unlock(*_):
        secret = password.get_text()
        if creating and secret != confirm.get_text():
            status.set_label("The master passwords do not match.")
            return
        password.set_text("")
        confirm.set_text("")

        def done(_):
            dialog.force_close()
            if vault.unlocked and not window._closing:
                completed()

        work = (lambda: vault.create(secret)) if creating else (lambda: vault.unlock(secret))
        run_task(window, dialog, status, [submit, password, confirm], work, done)

    submit.connect("clicked", unlock)
    password.connect("activate", unlock)
    confirm.connect("activate", unlock)
    toolbar.set_content(box)
    dialog.set_default_widget(submit)
    dialog.set_focus(password)
    dialog.present(window)


def passwords_dialog(window, *, import_now=False):
    ensure_unlocked(window, lambda: _passwords_dialog(window, import_now=import_now))


def _passwords_dialog(window, *, import_now):
    app = window.get_application()
    vault = app.vault
    dialog, toolbar = vault_shell("Passwords", height=620)
    box = padded_box()
    box.append(
        Gtk.Label(
            label="Export Chrome’s password CSV from Password Manager → Settings. "
            "Delete the CSV after import; it contains readable passwords.",
            wrap=True,
            xalign=0,
            css_classes=["dim-label", "caption"],
        )
    )
    search = Gtk.SearchEntry(placeholder_text="Search saved logins")
    box.append(search)
    rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
    rows.set_placeholder(
        Gtk.Label(
            label="No saved logins found",
            margin_top=24,
            margin_bottom=24,
            css_classes=["dim-label"],
        )
    )
    box.append(
        Gtk.ScrolledWindow(
            child=rows,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
            min_content_height=120,
        )
    )
    status = Gtk.Label(label="Locks after 5 minutes without password use.", wrap=True, xalign=0)
    box.append(status)
    import_button = Gtk.Button(
        label="Import Chrome password CSV…", css_classes=["suggested-action"]
    )
    box.append(import_button)
    change = Gtk.Button(label="Change master password…", css_classes=["flat"])
    box.append(change)
    lock = Gtk.Button(label="Lock vault", css_classes=["flat"])
    lock.connect("clicked", lambda *_: app.lock_vault())
    box.append(lock)
    controls = [search, rows, import_button, change, lock]

    def refresh(*_):
        if not vault.unlocked:
            dialog.force_close()
            return
        while rows.get_first_child():
            rows.remove(rows.get_first_child())
        query = search.get_text().casefold()
        for item in vault.summaries():
            if query not in f"{item['origin']} {item['username']} {item['name']}".casefold():
                continue
            row = Adw.ActionRow(
                title=item["username"] or "(no username)",
                subtitle=item["origin"],
                title_lines=1,
                subtitle_lines=1,
                use_markup=False,
            )
            delete = Gtk.Button(
                icon_name="user-trash-symbolic",
                tooltip_text="Delete saved login",
                valign=Gtk.Align.CENTER,
                css_classes=["flat"],
            )

            def confirm_delete(_button, record=item):
                question = Adw.AlertDialog(
                    heading="Delete saved login?",
                    body=f"{record['origin']}\n{record['username']}",
                )
                question._password_dialog = True
                question.add_responses("cancel", "Cancel", "delete", "Delete")
                question.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
                question.set_close_response("cancel")

                def response(_question, answer):
                    if answer == "delete":
                        run_task(
                            window,
                            dialog,
                            status,
                            controls,
                            lambda: vault.delete(record["origin"], record["username"]),
                            lambda _: (status.set_label("Saved login deleted."), refresh()),
                        )

                question.connect("response", response)
                question.present(window)

            delete.connect("clicked", confirm_delete)
            row.add_suffix(delete)
            rows.append(row)

    def choose_csv(*_):
        picker = Gtk.FileDialog(title="Choose Chrome password CSV")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        file_filter = Gtk.FileFilter(name="Password CSV")
        file_filter.add_pattern("*.csv")
        filters.append(file_filter)
        picker.set_filters(filters)

        def selected(picker, result):
            try:
                file = picker.open_finish(result)
                path = file.get_path()
                if not path:
                    status.set_label("Choose a local CSV file.")
                    return
            except GLib.Error:
                return

            def done(result):
                status.set_label(
                    f"Added {result.added:,}, updated {result.updated:,}, "
                    f"skipped {result.skipped:,}. "
                    "Delete the exported CSV when finished."
                )
                refresh()

            run_task(window, dialog, status, controls, lambda: vault.import_csv(Path(path)), done)

        picker.open(window, None, selected)

    import_button.connect("clicked", choose_csv)
    change.connect("clicked", lambda *_: change_password_dialog(window))
    search.connect("search-changed", refresh)
    toolbar.set_content(box)
    refresh()
    dialog.present(window)
    if import_now:
        choose_csv()
    return dialog


def change_password_dialog(window):
    vault = window.get_application().vault
    dialog, toolbar = vault_shell("Change master password")
    box = padded_box()
    entries = []
    for label in ["Current master password", "New master password", "Confirm new master password"]:
        box.append(Gtk.Label(label=label, xalign=0))
        entry = Gtk.PasswordEntry(show_peek_icon=True, activates_default=True)
        entries.append(entry)
        box.append(entry)
    status = Gtk.Label(label="Use at least 12 characters.", wrap=True, xalign=0)
    box.append(status)
    submit = Gtk.Button(label="Change password", css_classes=["suggested-action"])
    box.append(submit)

    def change(*_):
        current, new, confirmation = (entry.get_text() for entry in entries)
        if new != confirmation:
            status.set_label("The new master passwords do not match.")
            return
        for entry in entries:
            entry.set_text("")

        def done(_):
            dialog.force_close()
            window.toast("Master password changed")

        run_task(
            window,
            dialog,
            status,
            [submit, *entries],
            lambda: vault.change_password(current, new),
            done,
        )

    for entry in entries:
        entry.connect("activate", change)
    submit.connect("clicked", change)
    dialog.set_default_widget(submit)
    toolbar.set_content(box)
    dialog.present(window)


def fill_login(window):
    try:
        origin = credential_origin(window.webview.get_uri() or "")
        if (
            not can_fill_origin(origin)
            or window.failed
            or window.webview.is_loading()
            or window.stack.get_visible_child_name() != "web"
        ):
            raise VaultError("Open a secure login page before filling a password.")
    except VaultError as exc:
        window.toast(str(exc))
        return
    generation = window.document_generation

    def probed(view, result):
        try:
            value = view.call_async_javascript_function_finish(result)
            document = json.loads(value.to_string())
            if document["origin"] != origin or window.document_generation != generation:
                raise VaultError("The page changed. Try filling again.")
            ensure_unlocked(
                window, lambda: _choose_login(window, origin, document["token"], generation)
            )
        except (GLib.Error, ValueError, KeyError):
            window.toast("Could not inspect this login page.")
        except VaultError as exc:
            window.toast(str(exc))

    window.webview.call_async_javascript_function(
        autofill.PROBE,
        -1,
        None,
        autofill.WORLD,
        None,
        None,
        probed,
    )


def _choose_login(window, origin, token, generation):
    app = window.get_application()
    try:
        entries = app.vault.summaries(origin)
    except VaultError as exc:
        window.toast(str(exc))
        return
    if not entries:
        window.toast("No saved login for this exact website. Import Chrome passwords first.")
        return
    dialog, toolbar = vault_shell("Fill saved login")
    box = padded_box()
    box.append(Gtk.Label(label=origin, wrap=True, xalign=0, selectable=True))
    rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
    box.append(rows)

    def selected(_row, username):
        dialog.force_close()
        try:
            if (
                window.document_generation != generation
                or window.failed
                or credential_origin(window.webview.get_uri() or "") != origin
            ):
                raise VaultError("The page changed. Try filling again.")
            item = app.vault.credential(origin, username)
        except VaultError as exc:
            window.toast(str(exc))
            return
        arguments = GLib.Variant(
            "a{sv}",
            {
                "expectedOrigin": GLib.Variant("s", origin),
                "expectedDocument": GLib.Variant("s", token),
                "username": GLib.Variant("s", item.username),
                "password": GLib.Variant("s", item.password),
            },
        )
        cancellable = Gio.Cancellable()
        app.vault_requests.add(cancellable)

        def filled(view, result):
            app.vault_requests.discard(cancellable)
            try:
                code = view.call_async_javascript_function_finish(result).to_string()
                messages = {
                    "filled": "Saved login filled. Review the form, then sign in.",
                    "changed": "The page changed. Try filling again.",
                    "form": "Focus a visible login form with one password field, then try again.",
                    "action": "This form sends to another website. Password was not filled.",
                }
                window.toast(messages.get(code, "Could not fill this login form."))
            except GLib.Error:
                if not cancellable.is_cancelled():
                    window.toast("Could not fill this login form.")

        window.webview.call_async_javascript_function(
            autofill.FILL,
            -1,
            arguments,
            autofill.WORLD,
            None,
            cancellable,
            filled,
        )

    for entry in entries:
        row = Adw.ActionRow(
            title=entry["username"] or "(no username)",
            activatable=True,
            use_markup=False,
            title_lines=1,
        )
        row.connect("activated", selected, entry["username"])
        rows.append(row)
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=box)
    toolbar.set_content(scroll)
    dialog.present(window)
