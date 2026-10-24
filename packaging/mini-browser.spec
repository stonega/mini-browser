Name:           mini-browser
Version:        0.2.3
Release:        1%{?dist}
Summary:        A small native GNOME web browser
License:        GPL-3.0-or-later
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3 >= 3.11
BuildRequires:  python3-cryptography >= 44
BuildRequires:  desktop-file-utils
BuildRequires:  ruff

Requires:       python3 >= 3.11
Requires:       python3-gobject
Requires:       gtk4 >= 4.12
Requires:       libadwaita >= 1.5
Requires:       webkitgtk6.0 >= 2.40
Requires:       python3-cryptography >= 44

%description
A native GNOME browser with one page per portrait window, a native header
bar, Chrome bookmark and history import, and an encrypted local password
vault with Chrome password CSV import and login filling.

%prep
%autosetup

%build
# Python sources require no compilation step before installation.

%install
install -d %{buildroot}%{_datadir}/%{name}/mini_browser
install -m 0644 src/mini_browser/*.py %{buildroot}%{_datadir}/%{name}/mini_browser/
install -Dpm 0755 packaging/mini-browser %{buildroot}%{_bindir}/mini-browser
install -Dpm 0644 data/io.github.stone.MiniBrowser.desktop \
    %{buildroot}%{_datadir}/applications/io.github.stone.MiniBrowser.desktop
install -Dpm 0644 data/io.github.stone.MiniBrowser.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/io.github.stone.MiniBrowser.svg
install -Dpm 0644 packaging/mini-browser.1 %{buildroot}%{_mandir}/man1/mini-browser.1
install -d %{buildroot}%{_datadir}/gnome-shell/extensions/mini-browser-pin@io.github.stone
install -m 0644 data/gnome-shell/mini-browser-pin@io.github.stone/* \
    %{buildroot}%{_datadir}/gnome-shell/extensions/mini-browser-pin@io.github.stone/
/usr/bin/python3 -m compileall -q --invalidation-mode=checked-hash \
    -s %{buildroot} -p / %{buildroot}%{_datadir}/%{name}

%check
./scripts/check
desktop-file-validate %{buildroot}%{_datadir}/applications/io.github.stone.MiniBrowser.desktop

%files
%license COPYING
%doc README.md docs
%{_bindir}/mini-browser
%{_datadir}/%{name}/
%{_datadir}/applications/io.github.stone.MiniBrowser.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.stone.MiniBrowser.svg
%{_mandir}/man1/mini-browser.1*
%{_datadir}/gnome-shell/extensions/mini-browser-pin@io.github.stone/

%changelog
* Sat Oct 24 2026 Mini Browser contributors - 0.2.3-1
- Add audio/video layouts with immersive headers and intrinsic video sizing.
- Open new-tab links in the current window and add native Back navigation.
- Verify navigation and media layouts on an isolated virtual GNOME desktop.

* Fri Oct 16 2026 Mini Browser contributors - 0.2.2-1
- Simplify home to search and recent sites; move import controls into Settings.

* Fri Oct 16 2026 Mini Browser contributors - 0.2.1-1
- Package native header bar, six-digit PIN vault and Chrome data import.
