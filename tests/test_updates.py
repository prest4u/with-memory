from __future__ import annotations

import base64
import json
import os
import stat
import tempfile
import unittest
import urllib.request
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from eric_memory.errors import UpdateVerificationError
from eric_memory.updates import _GithubRedirectHandler, _install_version, _safe_extract, _verify_manifest


class SignedUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.private_key = Ed25519PrivateKey.generate()
        public_key = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.public_key_path = self.root / "release-public-key.pem"
        self.public_key_path.write_bytes(public_key)
        self.manifest = {
            "format": "with-release-v1",
            "version": "1.0.0",
            "schema_version": 2,
            "source_commit": "a" * 40,
            "assets": {"with-1.0.0-macos-arm64.zip": {"sha256": "0" * 64, "size": 100}},
        }
        self.manifest_bytes = json.dumps(self.manifest, sort_keys=True, separators=(",", ":")).encode()
        self.signature = self.private_key.sign(self.manifest_bytes)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_ed25519_signature_and_manifest_are_verified(self) -> None:
        result = _verify_manifest(
            self.manifest_bytes,
            base64.b64encode(self.signature),
            public_key_path=self.public_key_path,
            expected_version="1.0.0",
        )
        self.assertEqual(result, self.manifest)
        with self.assertRaises(UpdateVerificationError):
            _verify_manifest(
                self.manifest_bytes + b" ",
                self.signature,
                public_key_path=self.public_key_path,
                expected_version="1.0.0",
            )

    def _write_zip(self, name: str, members: list[tuple[zipfile.ZipInfo | str, bytes]]) -> Path:
        path = self.root / name
        with zipfile.ZipFile(path, "w") as archive:
            for member, payload in members:
                archive.writestr(member, payload)
        return path

    def test_archive_rejects_traversal_and_symlinks(self) -> None:
        traversal = self._write_zip("traversal.zip", [("../outside", b"x")])
        with self.assertRaises(UpdateVerificationError):
            _safe_extract(traversal, self.root / "traversal-output")

        link = zipfile.ZipInfo("eric-memory")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        symlink = self._write_zip("symlink.zip", [(link, b"/tmp/escape")])
        with self.assertRaises(UpdateVerificationError):
            _safe_extract(symlink, self.root / "symlink-output")

        windows_absolute = self._write_zip("windows-absolute.zip", [(r"C:\outside", b"x")])
        with self.assertRaises(UpdateVerificationError):
            _safe_extract(windows_absolute, self.root / "windows-absolute-output")

        windows_traversal = self._write_zip("windows-traversal.zip", [(r"..\outside", b"x")])
        with self.assertRaises(UpdateVerificationError):
            _safe_extract(windows_traversal, self.root / "windows-traversal-output")

    def test_redirect_is_rejected_before_requesting_an_unapproved_target(self) -> None:
        request = urllib.request.Request("https://github.com/prest4u/with-memory/releases/latest")
        handler = _GithubRedirectHandler()
        with self.assertRaises(UpdateVerificationError):
            handler.redirect_request(request, None, 302, "Found", {}, "http://github.com/insecure")
        with self.assertRaises(UpdateVerificationError):
            handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/payload")

    def test_versioned_install_activates_only_a_verified_onedir_payload(self) -> None:
        executable = zipfile.ZipInfo("with/eric-memory.exe" if os.name == "nt" else "with/eric-memory")
        executable.create_system = 3
        executable.external_attr = (stat.S_IFREG | 0o755) << 16
        archive = self._write_zip("valid.zip", [(executable, b"#!/bin/sh\nexit 0\n")])
        install_root = self.root / "install"
        result = _install_version(archive, root=install_root, version="1.0.0")
        self.assertTrue(Path(result["executable"]).is_file())
        self.assertTrue(Path(result["launcher"]).exists())
        current = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(current["version"], "1.0.0")
