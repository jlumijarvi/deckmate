import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vpnkey  # noqa: E402
from deckhid import KEY_PX  # noqa: E402

LISTING = "Wired:802-3-ethernet:activated\nOffice VPN:vpn:\nHome\\:wg:wireguard:activated\nlo:loopback:activated\n"


class ParseTest(unittest.TestCase):
    def test_listing(self):
        c = vpnkey.parse_connections(LISTING)
        self.assertEqual(c["Office VPN"], ("vpn", ""))
        self.assertEqual(c["Home:wg"], ("wireguard", "activated"))
        self.assertEqual(vpnkey.parse_connections("").keys().__len__(), 0)

    def test_vpns_and_state(self):
        c = vpnkey.Connections()
        c.all = vpnkey.parse_connections(LISTING)
        self.assertEqual(sorted(c.vpns()), ["Home:wg", "Office VPN"])
        self.assertEqual(c.state("Office VPN"), "")
        self.assertEqual(c.state("Home:wg"), "activated")
        self.assertIsNone(c.state("nope"))


class KeyTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        vpnkey.launch = self.calls.append
        vpnkey.sh = lambda cmd: LISTING
        vpnkey._connections = None

    def test_renders_every_state(self):
        for name in ("Office VPN", "Home:wg", "missing", ""):
            key = vpnkey.Vpn({"connection": name} if name else {})
            for pressed in (False, True):
                self.assertEqual(key.render(pressed).size, (KEY_PX, KEY_PX))

    def test_press_turns_off_an_active_and_on_an_inactive_connection(self):
        vpnkey.connections().refresh()
        vpnkey.Vpn({"connection": "Home:wg"}).press()
        vpnkey.Vpn({"connection": "Office VPN"}).press()
        vpnkey.Vpn({}).press()
        self.assertEqual(self.calls, ["nmcli connection down Home:wg", "nmcli connection up 'Office VPN'"])


if __name__ == "__main__":
    unittest.main()
