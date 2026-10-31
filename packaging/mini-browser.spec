Name:           mini-browser
Version:        0.4.4
Release:        1%{?dist}
Summary:        A small native GNOME web browser
License:        GPL-3.0-or-later AND Apache-2.0 AND BSD-3-Clause AND MIT AND BSD-2-Clause AND IJG AND Zlib
Source0:        %{name}-%{version}.tar.gz
ExclusiveArch:  x86_64 aarch64
%global cef_version 156.0.14+g4fa4ab5+chromium-156.0.8078.48
Source1:        https://cef-builds.spotifycdn.com/cef_binary_%{cef_version}_linux64_minimal.tar.bz2
Source2:        https://cef-builds.spotifycdn.com/cef_binary_%{cef_version}_linuxarm64_minimal.tar.bz2
%global cef_codec_version 156.0.8+g426920c+chromium-156.0.8078.48
Source3:        https://cef-builds.spotifycdn.com/cef_binary_%{cef_codec_version}_linux64_minimal.tar.bz2
Source4:        https://archive.archlinux.org/packages/c/cef/cef-156.0.8-1-x86_64.pkg.tar.zst
Source5:        https://archive.archlinux.org/packages/m/minizip/minizip-1:1.3.2-4-x86_64.pkg.tar.zst
Source6:        https://archive.archlinux.org/packages/s/simdutf/simdutf-9.2.1-2-x86_64.pkg.tar.zst
Source7:        https://archive.archlinux.org/packages/l/libjpeg-turbo/libjpeg-turbo-3.2.0-2-x86_64.pkg.tar.zst
Source8:        https://archive.archlinux.org/packages/o/openh264/openh264-2.7.0-1-x86_64.pkg.tar.zst
# This codec-enabled CEF requires glibc 2.44; retain the compatible official
# SDK on older Fedora versions and on ARM.
%global cef_codecs 0
%ifarch x86_64
%if 0%{?fedora} >= 45
%global cef_codecs 1
%endif
%endif
# CEF ships a prebuilt upstream runtime; retain it unmodified.
%global debug_package %{nil}
%global __strip /bin/true
# Bundled private libraries must not satisfy dependencies of unrelated packages.
%global __provides_exclude_from ^%{_libdir}/%{name}/cef/.*$
%global __requires_exclude ^((libcef|libmini_cef|libvk_swiftshader|libvulkan)\.so.*|libjpeg\.so\.8.*|libsimdutf\.so\.36.*|libminizip\.so\.1.*|libopenh264\.so\.8.*)$

BuildRequires:  cmake >= 3.21
BuildRequires:  ninja-build
BuildRequires:  gcc-c++
# The helper links libcef and its upstream Linux shared-library dependencies.
BuildRequires:  gtk3
BuildRequires:  nss
BuildRequires:  alsa-lib
BuildRequires:  mesa-libgbm
BuildRequires:  cups-libs
BuildRequires:  systemd-libs
BuildRequires:  python3 >= 3.11
BuildRequires:  python3-gobject
BuildRequires:  python3-cryptography >= 44
BuildRequires:  desktop-file-utils
BuildRequires:  ruff
%if %{cef_codecs}
BuildRequires:  zstd
BuildRequires:  at-spi2-atk
BuildRequires:  brotli
BuildRequires:  double-conversion
BuildRequires:  libxml2
BuildRequires:  libxslt
BuildRequires:  libwebp
BuildRequires:  opus
BuildRequires:  libdav1d
BuildRequires:  flac-libs
BuildRequires:  highway
BuildRequires:  pulseaudio-libs
%endif

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
%if %{cef_codecs}
mkdir -p build/cef-codec-packages
cp -p %{SOURCE4} %{SOURCE5} %{SOURCE6} %{SOURCE7} %{SOURCE8} build/cef-codec-packages/
./scripts/build-cef --codecs --archive %{SOURCE3}
%else
%ifarch x86_64
./scripts/build-cef --archive %{SOURCE1}
%else
./scripts/build-cef --archive %{SOURCE2}
%endif
%endif
# Load the actual packaged engine in every buildroot to catch missing or
# incompatible native dependencies before publishing an RPM.
/usr/bin/python3 -c 'import ctypes; ctypes.CDLL("build/cef/runtime/libmini_cef.so")'

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
install -Dpm 0644 src/mini_browser/icons/io.github.stone.MiniBrowser.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/io.github.stone.MiniBrowser.svg
install -Dpm 0644 packaging/mini-browser.1 %{buildroot}%{_mandir}/man1/mini-browser.1
install -d %{buildroot}%{_datadir}/gnome-shell/extensions/mini-browser-pin@io.github.stone
install -m 0644 data/gnome-shell/mini-browser-pin@io.github.stone/* \
    %{buildroot}%{_datadir}/gnome-shell/extensions/mini-browser-pin@io.github.stone/
/usr/bin/python3 -m compileall -q --invalidation-mode=checked-hash \
    -s %{buildroot} -p / %{buildroot}%{_datadir}/%{name}
%if %{cef_codecs}
# The checksum-pinned upstream libjpeg carries a standard /usr/lib RUNPATH.
# Preserve that binary; allow standard library paths while retaining every
# other RPM RPATH check (including insecure, empty and relative paths).
export QA_RPATHS=0x0001
%endif

%check
./scripts/check
desktop-file-validate %{buildroot}%{_datadir}/applications/io.github.stone.MiniBrowser.desktop

%files
%license COPYING
%if %{cef_codecs}
%license build/cef-sdk-codecs/LICENSE.txt
%else
%license build/cef-sdk/LICENSE.txt
%endif
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
* Sat Oct 31 2026 Mini Browser contributors - 0.4.4-1
- Reuse cached npm packages and metadata to avoid DeepSeek Harness startup stalls.

* Fri Oct 30 2026 Mini Browser contributors - 0.4.3-1
- Run CEF on its dedicated UI thread to eliminate the GTK idle busy loop.
- Stop hidden-view painting and release retained framebuffers while keeping audio alive.
- Center Home tabs and content within a shared responsive 600-pixel width.

* Thu Oct 29 2026 Mini Browser contributors - 0.4.2-1
- Add an Agent tab with a three-line prompt preview and session action records.
- Add per-site permissions and a native loading indicator; search with Google.
- Open cross-origin new-tab links in separate native windows.

* Thu Oct 29 2026 Mini Browser contributors - 0.4.1-1
- Hide the Home button on Home, including its History tab.
- Bundle the app icon so About resolves it in source and installed builds.

* Wed Oct 28 2026 Mini Browser contributors - 0.4.0-1
- Add opt-in agent CLI and native MCP control for Chromium and GTK.
- Unify Home apps, add persistent agent-created tiles, and simplify startup logs.
- Return app windows to a shared Home window and hide its page layout controls.

* Tue Oct 27 2026 Mini Browser contributors - 0.3.1-1
- Enable H.264/AAC web video on Fedora 45+ x86_64 with a pinned CEF runtime.
- Fix page Copy buttons, remember window sizes, and add the DeepSeek launcher.

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
