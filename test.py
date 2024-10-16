from falkordb import FalkorDB



client = FalkorDB(
    host="multizonesentinellblb.instance-mf7oswe84.hc-jx5tis6bc.us-central1.gcp.f2e0a955bb84.cloud",
    username="falkordb",
    password="falkordb",
    port=26379,
    ssl=False
)

db = client.select_graph('test')
print(db.query("CREATE (n:Person {name:'aaaa'}) RETURN n"))