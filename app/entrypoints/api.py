"""Entrypoint for the `api` Deployment.

Serves all HTTP routes: user REST (JWT), node API (mTLS), claiming.
Bound to 0.0.0.0:8000. Differentiation between auth models is done by
Ingress hostname + route-level dependencies.
"""

from __future__ import annotations

import uvicorn

from app.core.config import get_settings


def main() -> None:
    s = get_settings()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # noqa: S104 — container-bound
        port=8000,
        log_config=None,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
        workers=1 if s.debug else 2,
    )


if __name__ == "__main__":
    main()
