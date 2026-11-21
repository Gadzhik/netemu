#!/bin/sh
# NetEmu launcher for Linux.
#
# Qt needs a few X11/EGL system libraries that are not part of every distribution's default
# install.  Before starting the program this script checks them; when some are missing it says
# which ones, offers to install them with the distribution's package manager (apt, dnf, zypper
# or pacman) and only then starts NetEmu.
#
#   ./netemu.sh [project.netemu]          start (checks dependencies first)
#   ./netemu.sh --check-deps              only check and install dependencies
#   NETEMU_SKIP_DEPS=1 ./netemu.sh        start without the check
#   NETEMU_ASSUME_YES=1 ./netemu.sh       install missing libraries without asking

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HERE/NetEmu"

# library  apt-package  dnf/zypper-package  pacman-package
DEPS="libxcb-cursor.so.0       libxcb-cursor0        xcb-util-cursor        xcb-util-cursor
libxkbcommon-x11.so.0          libxkbcommon-x11-0    libxkbcommon-x11       libxkbcommon-x11
libEGL.so.1                    libegl1               mesa-libEGL            libglvnd
libGL.so.1                     libgl1                mesa-libGL             libglvnd
libxcb-icccm.so.4              libxcb-icccm4         xcb-util-wm            xcb-util-wm
libxcb-keysyms.so.1            libxcb-keysyms1       xcb-util-keysyms       xcb-util-keysyms
libxcb-image.so.0              libxcb-image0         xcb-util-image         xcb-util-image
libxcb-render-util.so.0        libxcb-render-util0   xcb-util-renderutil    xcb-util-renderutil
libxcb-shape.so.0              libxcb-shape0         libxcb                 libxcb
libxcb-randr.so.0              libxcb-randr0         libxcb                 libxcb
libxcb-xinerama.so.0           libxcb-xinerama0      libxcb                 libxcb
libfontconfig.so.1             libfontconfig1        fontconfig             fontconfig
libdbus-1.so.3                 libdbus-1-3           dbus-libs              dbus"

have_lib() {
    if command -v ldconfig >/dev/null 2>&1; then
        ldconfig -p 2>/dev/null | grep -q "$1" && return 0
    fi
    for d in /usr/lib /usr/lib64 /usr/lib/x86_64-linux-gnu /usr/lib/aarch64-linux-gnu /lib /lib64; do
        [ -e "$d/$1" ] && return 0
    done
    return 1
}

pkg_tool() {
    for t in apt-get dnf zypper pacman; do
        command -v "$t" >/dev/null 2>&1 && { echo "$t"; return; }
    done
}

column_for() {
    case "$1" in apt-get) echo 2 ;; dnf|zypper) echo 3 ;; pacman) echo 4 ;; *) echo 2 ;; esac
}

missing_packages() {
    tool="$1"; col=$(column_for "$tool")
    echo "$DEPS" | while read -r lib a b c; do
        [ -z "$lib" ] && continue
        have_lib "$lib" || echo "$DEPS" | awk -v l="$lib" -v c="$col" '$1 == l { print $c }'
    done | sort -u | tr '\n' ' '
}

install_cmd() {
    case "$1" in
        apt-get) echo "apt-get install -y $2" ;;
        dnf)     echo "dnf install -y $2" ;;
        zypper)  echo "zypper --non-interactive install $2" ;;
        pacman)  echo "pacman -S --needed --noconfirm $2" ;;
    esac
}

tell() {   # message to the user: dialog when started from the desktop, terminal otherwise
    if [ -t 1 ]; then
        printf '%s\n' "$1"
    elif command -v zenity >/dev/null 2>&1; then
        zenity --info --title="NetEmu" --width=480 --text="$1" 2>/dev/null
    elif command -v kdialog >/dev/null 2>&1; then
        kdialog --title "NetEmu" --msgbox "$1" 2>/dev/null
    else
        printf '%s\n' "$1" >&2
    fi
}

ask() {    # yes/no question, same channels as tell(); returns 0 for yes
    if [ "${NETEMU_ASSUME_YES:-0}" = "1" ]; then
        printf '%s\n(NETEMU_ASSUME_YES=1: yes)\n' "$1"
        return 0
    elif [ -t 0 ] && [ -t 1 ]; then
        printf '%s [Y/n] ' "$1"; read -r ans
        case "$ans" in n*|N*) return 1 ;; *) return 0 ;; esac
    elif command -v zenity >/dev/null 2>&1; then
        zenity --question --title="NetEmu" --width=480 --text="$1" 2>/dev/null
    elif command -v kdialog >/dev/null 2>&1; then
        kdialog --title "NetEmu" --yesno "$1" 2>/dev/null
    else
        return 1
    fi
}

check_deps() {
    tool=$(pkg_tool)
    pkgs=$(missing_packages "$tool")
    [ -z "$(echo "$pkgs" | tr -d ' ')" ] && return 0
    cmd=$(install_cmd "$tool" "$pkgs")
    if [ -z "$tool" ]; then
        tell "NetEmu needs system libraries that are not installed: $pkgs
Install them with your package manager and start NetEmu again."
        return 1
    fi
    if ! ask "NetEmu needs some system libraries to run properly, and they are not installed:

  $pkgs

Install them now? This runs (administrator password required):
  $cmd"; then
        tell "NetEmu may not start without these libraries. Install them later with:
  sudo $cmd"
        return 1
    fi
    if [ "$(id -u)" = "0" ]; then
        if [ "$tool" = "apt-get" ]; then apt-get update -qq; fi
        DEBIAN_FRONTEND=noninteractive sh -c "$cmd"
    elif [ -t 0 ] && command -v sudo >/dev/null 2>&1; then
        [ "$tool" = "apt-get" ] && sudo apt-get update -qq
        sudo sh -c "$cmd"
    elif command -v pkexec >/dev/null 2>&1; then
        if [ "$tool" = "apt-get" ]; then pkexec sh -c "apt-get update -qq && $cmd"; else pkexec sh -c "$cmd"; fi
    else
        tell "Run this command as administrator, then start NetEmu again:
  sudo $cmd"
        return 1
    fi
    left=$(missing_packages "$tool")
    if [ -n "$(echo "$left" | tr -d ' ')" ]; then
        tell "Some libraries are still missing: $left"
        return 1
    fi
    tell "The libraries NetEmu needs are installed."
    return 0
}

if [ "$1" = "--check-deps" ]; then
    check_deps
    exit $?
fi
[ "${NETEMU_SKIP_DEPS:-0}" = "1" ] || check_deps
exec "$BIN" "$@"
