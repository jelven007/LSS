"""Production rustdx provider using the in-process native transport."""
from __future__ import annotations

import os
from typing import Optional, Sequence

from .market_provider import DEFAULT_SERVERS, ClientFactory, Server, TdxMarketProvider
from .rustdx_client import RustdxNotReady, RustdxQuoteClient


class RustdxProvider(TdxMarketProvider):
    source_name = "rustdx"

    def __init__(
        self,
        history_sessions: int = 40,
        workers: int = 7,
        servers: Optional[Sequence[Server]] = None,
        client_factory: Optional[ClientFactory] = None,
    ) -> None:
        if servers is None:
            configured = os.environ.get("BANXIA_RUSTDX_SERVERS", "").strip()
            if configured:
                parsed = []
                for address in configured.split(","):
                    host, port = address.strip().rsplit(":", 1)
                    parsed.append((host, int(port)))
                servers = parsed
            else:
                servers = DEFAULT_SERVERS
        super().__init__(
            history_sessions=history_sessions,
            workers=workers,
            servers=servers,
            client_factory=client_factory or RustdxQuoteClient,
        )
