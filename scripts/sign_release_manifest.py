#!/usr/bin/env python3
"""Sign and immediately verify a release manifest using the offline Ed25519 key."""

from __future__ import annotations

import argparse
import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    private = serialization.load_pem_private_key(args.private_key.read_bytes(), password=None)
    public = serialization.load_pem_public_key(args.public_key.read_bytes())
    if not isinstance(private, Ed25519PrivateKey) or not isinstance(public, Ed25519PublicKey):
        raise SystemExit("release keys must be Ed25519")
    payload = args.manifest.read_bytes()
    signature = private.sign(payload)
    public.verify(signature, payload)
    args.output.write_bytes(base64.b64encode(signature) + b"\n")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
