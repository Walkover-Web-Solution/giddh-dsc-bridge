"""Regression test: a page-supplied certId must not choose the PKCS#11 library.

sign_hash() decodes a driver path from the certId the web page sends. Only a
driver the host already knows (detected or attached) may be used; any other
path must be ignored so a page can never make the host dlopen an arbitrary
local library.

Run from this folder (no token or PKCS#11 driver needed):
    python3 -m unittest test_sign_driver_validation
"""
import os
import tempfile
import unittest

import pkcs11_signer
from pkcs11_signer import IsolatedSigner, _encode_cert_id


class SignHashDriverValidation(unittest.TestCase):
    def setUp(self):
        # Two real files on disk: _decode_cert_id only returns paths that exist.
        self.tmp = tempfile.TemporaryDirectory()
        self.known = os.path.join(self.tmp.name, "known_driver.so")
        self.foreign = os.path.join(self.tmp.name, "not_a_driver.so")
        for p in (self.known, self.foreign):
            open(p, "w").close()

        # Keep ranking out of the way: the signer must keep our known path.
        self._rank = pkcs11_signer.rank_driver_candidates
        pkcs11_signer.rank_driver_candidates = lambda paths: list(paths)

        self.signer = IsolatedSigner([self.known])
        self.calls = []

        def fake_run_on_token(payload, timeout, budget, only_driver=None):
            self.calls.append({"payload": payload, "only_driver": only_driver})
            return {"signature": "SIG"}

        self.signer._run_on_token = fake_run_on_token

    def tearDown(self):
        pkcs11_signer.rank_driver_candidates = self._rank
        self.tmp.cleanup()

    def _sign(self, cert_id):
        return self.signer.sign_hash("AAAA", "SHA256", cert_id, "1234")

    def test_known_driver_tag_is_used(self):
        self.assertEqual(self._sign(_encode_cert_id(self.known, "ab12")), "SIG")
        self.assertEqual(self.calls[-1]["only_driver"], self.known)
        self.assertEqual(self.calls[-1]["payload"]["certId"], "ab12")

    def test_unknown_driver_tag_is_ignored(self):
        self._sign(_encode_cert_id(self.foreign, "ab12"))
        self.assertIsNone(self.calls[-1]["only_driver"],
                          "a page-supplied path outside driver_paths must never be loaded")
        self.assertEqual(self.calls[-1]["payload"]["certId"], "ab12")

    def test_untagged_cert_id_probes_all_drivers(self):
        self._sign("ab12")
        self.assertIsNone(self.calls[-1]["only_driver"])
        self.assertEqual(self.calls[-1]["payload"]["certId"], "ab12")


if __name__ == "__main__":
    unittest.main()
