#!/bin/sh
# Rebuild ../AgentDock.exe (needs mingw-w64: apt install gcc-mingw-w64-x86-64, or MSYS2 on Windows).
set -e
cd "$(dirname "$0")"
x86_64-w64-mingw32-windres launcher.rc -O coff -o launcher.res
x86_64-w64-mingw32-gcc -O2 -s -municode -mwindows -finput-charset=UTF-8 -o ../AgentDock.exe launcher.c launcher.res
rm -f launcher.res
