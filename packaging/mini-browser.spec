Name:           mini-browser
Version:        0.2.1
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

%changelog
* Fri Oct 16 2026 Mini Browser contributors - 0.2.1-1
- Package native header bar, six-digit PIN vault and Chrome data import.
