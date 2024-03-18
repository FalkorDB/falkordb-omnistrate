import sys
import redis
from redis.sentinel import Sentinel
from time import sleep, time

SENTINEL_HOST = sys.argv[1] if len(sys.argv) > 1 else "localhost"
SENTINEL_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 26379
ADMIN_PASSWORD = sys.argv[3] if len(sys.argv) > 3 else "admin"

MAX_FAILOVER_TIME_SECONDS = 10


def test_failover():

    sentinel = Sentinel(
        [(SENTINEL_HOST, SENTINEL_PORT)],
        sentinel_kwargs={"password": ADMIN_PASSWORD},
        connection_kwargs={"password": ADMIN_PASSWORD},
    )

    retries = 0
    last_time = 0
    failover_triggered_time = 0

    while True:
        try:
            master = sentinel.discover_master("master")
            if (master is None) or (len(master) < 2):
                print("No master found")
                break
            r = redis.StrictRedis(
                host=master[0], port=master[1], password=ADMIN_PASSWORD, db=0
            )

            if retries > 0:
                if r.get("time") != str(last_time).encode("utf-8"):
                    print(
                        "Data lost: "
                        + str(r.get("time"))
                        + " != "
                        + str(last_time)
                        + " retries: "
                        + str(retries)
                    )
                    break
                print(
                    "Failover successful. Took " + str(time() - last_time) + " seconds"
                )
                break
            
            tmp = last_time
            last_time = time()
            try:
                r.set("time", last_time)
            except Exception as e:
                last_time = tmp
                raise e
            if r.get("time") != str(last_time).encode("utf-8"):
                print("Data lost: " + str(r.get("time")) + " != " + str(last_time))
                break
            sleep(0.5)
        except Exception as e:
            print("Could not connect", e)
            if failover_triggered_time == 0:
                failover_triggered_time = time()
            retries += 1
            if (time() - failover_triggered_time) > MAX_FAILOVER_TIME_SECONDS:
                print("Failover took too long: " + str(time() - failover_triggered_time))
            sleep(0.5)
            pass
        

if __name__ == "__main__":
    test_failover()
