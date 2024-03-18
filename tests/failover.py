import redis
from redis.sentinel import Sentinel
from time import sleep

SENTINEL_HOST = (
    "r-q3fscdg4lc.instance-t1mjddzwl.hc-uxl2s51wd.us-east1.gcp.f2e0a955bb84.cloud"
)
SENTINEL_PORT = "26379"
ADMIN_PASSWORD = "rZyD2N7UsFwzkb"

SLEEP_SECONDS = 2
MAX_FAILOVER_TIME_SECONDS = 10


def test_failover():

    sentinel = Sentinel([(SENTINEL_HOST, SENTINEL_PORT)], sentinel_kwargs={"password": ADMIN_PASSWORD}, connection_kwargs={"password": ADMIN_PASSWORD})

    retries = 0

    while True:
        try:
            master = sentinel.discover_master("master")
            r = redis.StrictRedis(
                host=master[0], port=master[1], password=ADMIN_PASSWORD, db=0
            )
            r.set("foo", "bar")
            assert r.get("foo") == b"bar"

            if retries > 0:
                print("Failover successful")
                break

            sleep(SLEEP_SECONDS)
        except:
            print("Could not connect")
            retries += 1
            if retries > MAX_FAILOVER_TIME_SECONDS / SLEEP_SECONDS:
                raise Exception("Failover took too long")
            pass


if __name__ == "__main__":
    test_failover()
    print("Failover test passed")
