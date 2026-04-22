import base64
import json
from datetime import timedelta

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import LicenseConfig
from .services import canonicalize_payload, refresh_license_status, verify_license_blob


class LicenseServicesTests(TestCase):
    def setUp(self):
        private_key = Ed25519PrivateKey.generate()
        self.public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        self.private_key = private_key

    def _build_license_text(self, **payload_overrides):
        today = timezone.localdate()
        payload = {
            "license_id": "LIC-001",
            "customer_name": "Test zákazník",
            "license_type": "subscription",
            "valid_from": today.isoformat(),
            "valid_until": (today + timedelta(days=30)).isoformat(),
            "support_until": (today + timedelta(days=10)).isoformat(),
            "modules": ["ankety", "pokladna"],
        }
        payload.update(payload_overrides)
        signature = self.private_key.sign(canonicalize_payload(payload))
        return json.dumps(
            {
                "version": 1,
                "payload": payload,
                "signature": base64.b64encode(signature).decode("ascii"),
            },
            ensure_ascii=False,
        )

    @override_settings(LICENSE_PUBLIC_KEY="", LICENSE_PUBLIC_KEY_PATH="/tmp/neexistuje-public-key.pem")
    def test_missing_public_key_file_raises_without_setting(self):
        with self.assertRaises(FileNotFoundError):
            verify_license_blob(self._build_license_text())

    @override_settings()
    def test_refresh_license_status_marks_active_license(self):
        with override_settings(LICENSE_PUBLIC_KEY=self.public_key, LICENSE_ENFORCEMENT=True):
            config = LicenseConfig.objects.create(license_blob=self._build_license_text())
            result = refresh_license_status(config)
            self.assertTrue(result.is_valid)
            self.assertEqual(result.status, LicenseConfig.STATUS_ACTIVE)
            self.assertEqual(config.status, LicenseConfig.STATUS_ACTIVE)
