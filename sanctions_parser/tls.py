from __future__ import annotations

import truststore

_NATIVE_TRUST_ENABLED = False


def enable_native_trust_store() -> None:
    """Make HTTPS clients use the operating system's trusted certificates.

    On managed Windows devices this includes enterprise certificate authorities
    installed by IT for approved HTTPS inspection products such as GlobalProtect.
    Certificate and hostname verification remain enabled.
    """
    global _NATIVE_TRUST_ENABLED
    if _NATIVE_TRUST_ENABLED:
        return
    truststore.inject_into_ssl()
    _NATIVE_TRUST_ENABLED = True
