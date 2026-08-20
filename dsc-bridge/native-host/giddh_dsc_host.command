#!/bin/sh
# Dev launcher for the Giddh DSC native messaging host.
#
# Chrome execs this directly, so it must write NOTHING to stdout except the
# native-messaging protocol itself (4-byte length prefix + JSON).
#
# The interpreter is pinned deliberately: /usr/bin/python3 does NOT have the
# python-pkcs11 dependency installed, so relying on `env python3` (whatever
# happens to be first on Chrome's PATH) fails with ModuleNotFoundError and
# looks like a broken bridge.
exec /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    "$(dirname "$0")/giddh_dsc_host.py" "$@"
