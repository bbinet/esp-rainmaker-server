#!/usr/bin/env python3
"""Simulator reproducing the firmware (`esp-rainmaker`) side of the
contract end-to-end against a running backend.

Subcommands:

  provision-key MAC PLATFORM        register a per-device HMAC key in
                                    device_provisioning (admin op,
                                    direct DB insert).

  self-claim --mac --platform       run /claim/initiate + /claim/verify
                                    using the provisioned HMAC key.

  run --cert-dir DIR                connect MQTT mTLS to the broker
                                    with the device cert, publish
                                    config + initial params, subscribe
                                    to params/remote (echo into
                                    params/local), publish tsdata
                                    every 30s, listen for otaurl /
                                    to-node.

  publish-mapping --cert-dir DIR
                  --user-id UUID
                  --secret-key STR  publish node/<id>/user/mapping so
                                    the backend can finalise the
                                    user-node mapping.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import ssl
import sys
import time
import urllib.request
from pathlib import Path

import aiomqtt
import httpx
import paho.mqtt.client as paho
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _api_base() -> str:
    return os.environ.get("FAKE_NODE_API", "http://localhost:8000")


def _http_client(*, timeout: float = 10.0) -> httpx.Client:
    """Build an httpx client respecting FAKE_NODE_CA_BUNDLE if set."""
    ca = os.environ.get("FAKE_NODE_CA_BUNDLE")
    verify: bool | str = ca if ca else True
    return httpx.Client(timeout=timeout, verify=verify)


def _mqtt_broker() -> tuple[str, int]:
    host = os.environ.get("FAKE_NODE_MQTT_HOST", "localhost")
    port = int(os.environ.get("FAKE_NODE_MQTT_PORT", "8883"))
    return host, port


def _ca_path() -> Path:
    return Path(os.environ.get("FAKE_NODE_CA_CHAIN", "var/pki/ca-chain.pem"))


def _device_dir(node_id: str) -> Path:
    return Path("var/devices") / node_id


def _generate_csr(common_name: str) -> tuple[bytes, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, common_name)])
    csr = x509.CertificateSigningRequestBuilder().subject_name(subject).sign(key, hashes.SHA256())
    return csr.public_bytes(serialization.Encoding.PEM), key


def cmd_provision_key(args: argparse.Namespace) -> None:
    """Insert a DeviceProvisioning row directly. Manufacturer-side op."""
    import psycopg  # type: ignore[import-not-found]

    dsn = os.environ.get(
        "FAKE_NODE_DB_DSN",
        "postgresql://rainmaker:rainmaker@localhost:5432/rainmaker",
    )
    key = secrets.token_bytes(32) if args.key is None else bytes.fromhex(args.key)

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO device_provisioning (mac_addr, platform, hmac_key) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (mac_addr) DO UPDATE SET hmac_key = EXCLUDED.hmac_key",
            (args.mac, args.platform, key),
        )

    out_dir = _device_dir(args.mac.lower())
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "hmac.key").write_bytes(key)
    (out_dir / "hmac.key").chmod(0o600)
    print(f"Provisioned {args.mac} ({args.platform}); key={key.hex()}")


def cmd_self_claim(args: argparse.Namespace) -> None:
    out_dir = _device_dir(args.mac.lower())
    hmac_key_path = out_dir / "hmac.key"
    if not hmac_key_path.exists():
        sys.exit(f"HMAC key {hmac_key_path} missing; run provision-key first.")
    key = hmac_key_path.read_bytes()
    api = _api_base()

    client = _http_client()
    init = client.post(
        f"{api}/claim/initiate",
        json={"mac_addr": args.mac, "platform": args.platform},
    )
    if init.status_code != 200:
        sys.exit(f"initiate failed: {init.status_code} {init.text}")
    init_body = init.json()
    auth_id = init_body["auth_id"]
    challenge = base64.b64decode(init_body["challenge"])

    node_id = args.mac.lower()
    csr_pem, device_key = _generate_csr(node_id)

    proof = hmac.new(key, challenge, hashlib.sha256).digest()
    verify = client.post(
        f"{api}/claim/verify",
        json={
            "auth_id": auth_id,
            "challenge_response": base64.b64encode(proof).decode(),
            "csr": csr_pem.decode(),
            "send_mqtt_host": True,
        },
    )
    if verify.status_code != 200:
        sys.exit(f"verify failed: {verify.status_code} {verify.text}")
    body = verify.json()
    cert_pem = body["certificate"].encode()

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "node.pem").write_bytes(cert_pem)
    (out_dir / "node.key").write_bytes(
        device_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (out_dir / "node.key").chmod(0o600)
    (out_dir / "metadata.json").write_text(
        json.dumps(
            {
                "node_id": node_id,
                "mac_addr": args.mac,
                "platform": args.platform,
                "mqtt_host": body.get("mqtt_host"),
            },
            indent=2,
        )
    )
    print(f"verify: cert saved to {out_dir / 'node.pem'}; node_id={node_id}")


def _load_device(cert_dir: Path) -> dict:
    meta = json.loads((cert_dir / "metadata.json").read_text())
    return {
        "node_id": meta["node_id"],
        "cert_path": cert_dir / "node.pem",
        "key_path": cert_dir / "node.key",
    }


def _build_ssl_context(cert: Path, key: Path) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=str(_ca_path()))
    ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
    return ctx


async def cmd_run(args: argparse.Namespace) -> None:
    dev = _load_device(Path(args.cert_dir))
    node_id = dev["node_id"]
    host, port = _mqtt_broker()
    ctx = _build_ssl_context(dev["cert_path"], dev["key_path"])

    config_payload = {
        "node_id": node_id,
        "config_version": "2019-09-11",
        "info": {
            "name": "FakeBulb",
            "fw_version": "1.0.0",
            "type": "Lightbulb",
            "model": "fake-bulb-v1",
            "project_name": "fake_node",
            "platform": "esp32s3",
        },
        "devices": [
            {
                "name": "Light",
                "type": "esp.device.lightbulb",
                "primary": "power",
                "params": [
                    {
                        "name": "power",
                        "type": "esp.param.power",
                        "data_type": "bool",
                        "properties": ["read", "write"],
                    },
                    {
                        "name": "brightness",
                        "type": "esp.param.brightness",
                        "data_type": "int",
                        "properties": ["read", "write", "time_series"],
                        "bounds": {"min": 0, "max": 100, "step": 1},
                    },
                ],
            }
        ],
        "services": [],
    }
    state = {"Light": {"power": False, "brightness": 50}}

    print(f"connecting to {host}:{port} as {node_id}")
    async with aiomqtt.Client(
        hostname=host,
        port=port,
        identifier=node_id,
        tls_context=ctx,
        clean_session=False,
        protocol=paho.MQTTv311,
    ) as client:
        print("connected, publishing config")
        await client.publish(f"node/{node_id}/config", json.dumps(config_payload).encode(), qos=1)
        await client.publish(f"node/{node_id}/params/local/init", json.dumps(state).encode(), qos=1)
        await client.subscribe(f"node/{node_id}/params/remote", qos=1)
        await client.subscribe(f"node/{node_id}/otaurl", qos=1)
        await client.subscribe(f"node/{node_id}/to-node", qos=1)

        async def tsdata_loop() -> None:
            while True:
                await asyncio.sleep(30)
                point = {
                    "ts_data_version": "2021-09-13",
                    "ts_data": [
                        {
                            "name": "Light.brightness",
                            "type": "esp.param.brightness",
                            "dt": "int",
                            "t": int(time.time()),
                            "records": [{"t": int(time.time()), "v": state["Light"]["brightness"]}],
                        }
                    ],
                }
                await client.publish(f"node/{node_id}/tsdata", json.dumps(point).encode(), qos=1)
                print(f"tsdata: brightness={state['Light']['brightness']}")

        ts_task = asyncio.create_task(tsdata_loop())
        try:
            async for msg in client.messages:
                topic = msg.topic.value
                payload = json.loads(msg.payload.decode())
                if topic.endswith("/params/remote"):
                    print(f"recv params/remote: {payload}")
                    for dev_name, params in payload.items():
                        if isinstance(params, dict):
                            state.setdefault(dev_name, {}).update(params)
                    await client.publish(
                        f"node/{node_id}/params/local", json.dumps(state).encode(), qos=1
                    )
                    print(f"echoed params/local: {state}")
                elif topic.endswith("/otaurl"):
                    print(f"recv otaurl: {payload}")
                    job_id = payload.get("ota_job_id")
                    url = payload.get("url")
                    if url and job_id:
                        try:
                            urllib.request.urlopen(url, timeout=5).read()  # noqa: S310, ASYNC210
                            await client.publish(
                                f"node/{node_id}/otastatus",
                                json.dumps(
                                    {
                                        "ota_job_id": job_id,
                                        "status": "success",
                                        "ts": int(time.time()),
                                    }
                                ).encode(),
                                qos=1,
                            )
                            print(f"reported OTA success for {job_id}")
                        except Exception as exc:  # noqa: BLE001
                            print(f"ota download failed: {exc}")
                elif topic.endswith("/to-node"):
                    print(f"recv to-node: {payload}")
        finally:
            ts_task.cancel()


async def cmd_publish_mapping(args: argparse.Namespace) -> None:
    dev = _load_device(Path(args.cert_dir))
    node_id = dev["node_id"]
    host, port = _mqtt_broker()
    ctx = _build_ssl_context(dev["cert_path"], dev["key_path"])

    body = {
        "node_id": node_id,
        "user_id": args.user_id,
        "secret_key": args.secret_key,
        "reset": False,
    }
    print(f"publishing user/mapping: {body}")
    async with aiomqtt.Client(
        hostname=host,
        port=port,
        identifier=f"{node_id}-mapping",
        tls_context=ctx,
        protocol=paho.MQTTv311,
    ) as client:
        await client.publish(f"node/{node_id}/user/mapping", json.dumps(body).encode(), qos=1)
        await asyncio.sleep(1)
    print("mapping published")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fake ESP RainMaker firmware simulator")
    sub = p.add_subparsers(dest="cmd", required=True)

    prov = sub.add_parser("provision-key", help="Manufacturer-side: register HMAC key")
    prov.add_argument("mac")
    prov.add_argument("platform")
    prov.add_argument("--key", help="Hex-encoded 32-byte key (random if omitted)")

    claim = sub.add_parser("self-claim", help="Run /claim/initiate + /claim/verify")
    claim.add_argument("--mac", required=True)
    claim.add_argument("--platform", required=True)

    run_c = sub.add_parser("run", help="Connect MQTT and run the firmware loop")
    run_c.add_argument("--cert-dir", required=True)

    pm = sub.add_parser("publish-mapping", help="Publish node/<id>/user/mapping")
    pm.add_argument("--cert-dir", required=True)
    pm.add_argument("--user-id", required=True)
    pm.add_argument("--secret-key", required=True)

    return p


def main() -> None:
    args = build_parser().parse_args()
    if args.cmd == "provision-key":
        cmd_provision_key(args)
    elif args.cmd == "self-claim":
        cmd_self_claim(args)
    elif args.cmd == "run":
        asyncio.run(cmd_run(args))
    elif args.cmd == "publish-mapping":
        asyncio.run(cmd_publish_mapping(args))


if __name__ == "__main__":
    main()
