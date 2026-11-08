#!/bin/sh
here="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$here/bin/herdr-epic" install "$@"
