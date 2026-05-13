import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class LdapTierActivationTests(unittest.TestCase):
    def _assert_ldap_enabled(self, compose_file: str):
        content = (REPO_ROOT / "compose" / compose_file).read_text()
        self.assertIn("- LDAP_ENABLED=true", content)
        self.assertIn(
            "- LDAP_AUTH_SERVER_URL=ldaps://ldap-auth-service.ldap-auth.svc.cluster.local:3390",
            content,
        )

    def test_startup_tier_enables_ldap(self):
        self._assert_ldap_enabled("omnistrate.startup.yaml")

    def test_pro_tier_enables_ldap(self):
        self._assert_ldap_enabled("omnistrate.pro.yaml")


if __name__ == "__main__":
    unittest.main()
