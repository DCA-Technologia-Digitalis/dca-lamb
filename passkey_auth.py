"""WebAuthn helpers for passwordless passkey authentication."""

import hmac
from urllib.parse import urlsplit

from fido2.server import Fido2Server
from fido2.webauthn import PublicKeyCredentialRpEntity


def server_for_request(config, host, scheme):
    configured_origin = config.get("WEBAUTHN_ORIGIN")
    origin = configured_origin or f"{scheme}://{host}"
    parsed_origin = urlsplit(origin)
    if (
        parsed_origin.scheme not in {"http", "https"}
        or not parsed_origin.hostname
        or parsed_origin.path not in {"", "/"}
        or parsed_origin.query
        or parsed_origin.fragment
    ):
        raise ValueError("Configure WEBAUTHN_ORIGIN as a valid site origin.")

    rp_id = config.get("WEBAUTHN_RP_ID") or parsed_origin.hostname
    rp_id = rp_id.lower().rstrip(".")
    origin_host = parsed_origin.hostname.lower().rstrip(".")
    if origin_host != rp_id and not origin_host.endswith(f".{rp_id}"):
        raise ValueError("WEBAUTHN_RP_ID does not match the site origin.")
    if parsed_origin.scheme != "https" and origin_host != "localhost":
        raise ValueError("Passkeys require HTTPS except on localhost.")

    expected_origin = origin.rstrip("/")
    return Fido2Server(
        PublicKeyCredentialRpEntity(
            name=config.get("WEBAUTHN_RP_NAME", "DV Odonto"),
            id=rp_id,
        ),
        verify_origin=lambda supplied: hmac.compare_digest(
            supplied, expected_origin
        ),
    )
