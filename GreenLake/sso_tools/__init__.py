"""SSO Tools (Okta role string generator + SAML metadata checker), mounted at ``/sso-tools``."""

from .webapp import build_sso_tools_app

__all__ = ["build_sso_tools_app"]
