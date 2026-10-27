Name:           mini-browser
Version:        0.3.0
Release:        1%{?dist}
Summary:        A small native GNOME web browser
License:        GPL-3.0-or-later AND Apache-2.0 AND BSD-3-Clause
Source0:        %{name}-%{version}.tar.gz
ExclusiveArch:  x86_64 aarch64
%global cef_version 156.0.14+g4fa4ab5+chromium-156.0.8078.48
Source1:        https://cef-builds.spotifycdn.com/cef_binary_%{cef_version}_linux64_minimal.tar.bz2
Source2:        https://cef-builds.spotifycdn.com/cef_binary_%{cef_version}_linuxarm64_minimal.tar.bz2
# CEF ships a prebuilt upstream runtime; retain it unmodified.
%global debug_package %{nil}
%global __strip /bin/true
# Bundled private libraries must not satisfy dependencies of unrelated packages.
%global __provides_exclude_from ^%{_libdir}/%{name}/cef/.*$
%global __requires_exclude ^(libcef|libmini_cef|libvk_swiftshader|libvulkan)\.so.*$

BuildRequires:  cmake >= 3.21
BuildRequires:  ninja-build
BuildRequires:  gcc-c++
BuildRequires:  python3 >= 3.11
BuildRequires:  python3-cryptography >= 44
BuildRequires:  desktop-file-utils
BuildRequires:  ruff

Requires:       python3 >= 3.11
Requires:       python3-gobject
Requires:       gtk4 >= 4.12
Requires:       libadwaita >= 1.5
Requires:       nss
Requires:       nspr
Requires:       alsa-lib
Requires:       mesa-libgbm
Requires:       liberation-fonts
Requires:       python3-cryptography >= 44
Requires:       android-tools
Requires:       gstreamer1
Requires:       gstreamer1-plugins-base
Requires:       gstreamer1-plugins-good
Requires:       gstreamer1-plugins-bad-free
Requires:       gstreamer1-plugin-gtk4
Recommends:     gstreamer1-plugin-openh264
Suggests:       uxplay >= 1.73

%description
A native GNOME browser with one page per portrait window, a native header
bar, Chrome bookmark and history import, and an encrypted local password
vault with Chrome password CSV import and login filling.

%prep
%autosetup

%build
%ifarch x86_64
./scripts/build-cef --archive %{SOURCE1}
%else
./scripts/build-cef --archive %{SOURCE2}
%endif

%install
install -d %{buildroot}%{_datadir}/%{name}/mini_browser
install -m 0644 src/mini_browser/*.py %{buildroot}%{_datadir}/%{name}/mini_browser/
install -d %{buildroot}%{_datadir}/%{name}/mini_browser/icons
install -m 0644 src/mini_browser/icons/*.svg \
    %{buildroot}%{_datadir}/%{name}/mini_browser/icons/
install -d %{buildroot}%{_datadir}/%{name}/mini_browser/vendor/scrcpy
install -m 0644 src/mini_browser/vendor/scrcpy/* \
    %{buildroot}%{_datadir}/%{name}/mini_browser/vendor/scrcpy/
install -d %{buildroot}%{_libdir}/%{name}/cef
cp -aL build/cef/runtime/. %{buildroot}%{_libdir}/%{name}/cef/
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
%license build/cef-sdk/LICENSE.txt
%license src/mini_browser/vendor/scrcpy/LICENSE src/mini_browser/vendor/scrcpy/NOTICE
%doc README.md docs
%{_bindir}/mini-browser
%{_datadir}/%{name}/
%{_libdir}/%{name}/
%{_datadir}/applications/io.github.stone.MiniBrowser.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.stone.MiniBrowser.svg
%{_mandir}/man1/mini-browser.1*
%{_datadir}/gnome-shell/extensions/mini-browser-pin@io.github.stone/

%changelog
* Tue Oct 27 2026 Mini Browser contributors - 0.3.0-1
- Replace WebKitGTK with sandboxed Chromium while retaining native GTK windows.
- Add Refresh Page and cache-bypassing reload to the main menu.

* Sun Oct 25 2026 Mini Browser contributors - 0.2.5-1
- Document COPR installation, updates, and verified source RPM publishing.

* Sat Oct 24 2026 Mini Browser contributors - 0.2.4-1
- Add Android and iPhone mirroring with a bundled, checksum-pinned scrcpy server.
- Discover connected Android phones in a Devices group and merge USB/Wi-Fi aliases.
- Add window pinning, Back history, and Home fallback; widen the audio player.

* Sat Oct 24 2026 Mini Browser contributors - 0.2.3-3
- Group phones under Devices and deduplicate USB/Wi-Fi aliases by hardware identity.

* Sat Oct 24 2026 Mini Browser contributors - 0.2.3-2
- Bundle the checksum-pinned scrcpy Android server and install ADB as a dependency.
- Show connected Android phones on Home automatically.

* Sat Oct 24 2026 Mini Browser contributors - 0.2.3-1
- Add audio/video layouts with immersive headers and intrinsic video sizing.
- Open new-tab links in the current window and add native Back navigation.
- Verify navigation and media layouts on an isolated virtual GNOME desktop.

* Fri Oct 16 2026 Mini Browser contributors - 0.2.2-1
- Simplify home to search and recent sites; move import controls into Settings.

* Fri Oct 16 2026 Mini Browser contributors - 0.2.1-1
- Package native header bar, six-digit PIN vault and Chrome data import.
