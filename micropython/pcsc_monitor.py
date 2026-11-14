"""Print card insert/remove events from the PC/SC reader for N seconds (default 45)."""
import sys
import time

from smartcard.CardMonitoring import CardMonitor, CardObserver
from smartcard.util import toHexString

t0 = time.time()


class Obs(CardObserver):
    def update(self, observable, actions):
        added, removed = actions
        for c in added:
            print("%5.1fs  INSERTED  ATR %s" % (time.time() - t0, toHexString(c.atr)), flush=True)
        for c in removed:
            print("%5.1fs  REMOVED" % (time.time() - t0), flush=True)


mon = CardMonitor()
obs = Obs()
mon.addObserver(obs)
print("watching...", flush=True)
time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 45)
mon.deleteObserver(obs)
