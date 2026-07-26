from unittest.mock import patch

from sanctions_parser import tls


def test_native_trust_store_is_enabled_only_once() -> None:
    original_state = tls._NATIVE_TRUST_ENABLED
    tls._NATIVE_TRUST_ENABLED = False
    try:
        with patch.object(tls.truststore, "inject_into_ssl") as inject:
            tls.enable_native_trust_store()
            tls.enable_native_trust_store()
        inject.assert_called_once_with()
    finally:
        tls._NATIVE_TRUST_ENABLED = original_state
