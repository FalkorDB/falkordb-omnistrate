import sys
from time import sleep, time
from falkordb import FalkorDB

NODE_HOST = sys.argv[1] if len(sys.argv) > 1 else "localhost"
NODE_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 6379
ADMIN_PASSWORD = sys.argv[3] if len(sys.argv) > 3 else "admin"

MAX_FAILOVER_TIME_SECONDS = 10

node_id = -1

def test_failover():

    falkordb = FalkorDB(
        host=NODE_HOST,
        port=NODE_PORT,
        password=ADMIN_PASSWORD,
    )

    retries = 0
    last_time = 0
    failover_triggered_time = 0

    while True:
        try:
            graph = falkordb.select_graph("test")


            if retries > 0:
                graph_time = read_time(graph)
                if str(graph_time).encode('utf-8') != str(last_time).encode("utf-8"):
                    print(
                        "Data lost: "
                        + str(graph_time)
                        + " != "
                        + str(last_time)
                        + " retries: "
                        + str(retries)
                    )
                    break
                print(
                    "Failover successful. Took " + str(float(str(time())) - float(str(last_time))) + " seconds"
                )
                break

            tmp = last_time
            last_time = f'{time()}'
            try:
                write_time(graph, last_time)
            except Exception as e:
                last_time = tmp
                raise e
            
            graph_time = read_time(graph)
            if str(graph_time).encode('utf-8') != str(last_time).encode("utf-8"):
                print("Data lost: " + str(graph_time) + " != " + str(last_time))
                break
            sleep(0.5)
        except Exception as e:
            print("Could not connect", e)
            if failover_triggered_time == 0:
                failover_triggered_time = time()
            retries += 1
            if (time() - failover_triggered_time) > MAX_FAILOVER_TIME_SECONDS:
                print(
                    "Failover took too long: " + str(time() - failover_triggered_time)
                )
            sleep(0.5)

def write_time(graph, t):
    # If node exists, update it
    # print(f"Writing time: {t}")
    global node_id
    if node_id == -1:
        response = graph.query(f"CREATE (n:Time {{time: '{t}'}}) RETURN id(n)")
        node_id = response.result_set[0][0]
    else:
        graph.query(f"MATCH (n:Time) WHERE id(n) = {node_id} SET n.time = '{t}'")


def read_time(graph):
    response = graph.query("MATCH (n:Time) RETURN n.time")
    # print(f"Read time: {response.result_set[0][0]}")
    return f'{response.result_set[0][0]}'


if __name__ == "__main__":
    test_failover()
