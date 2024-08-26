#!/bin/bash -e

apt update && apt install redis inetutils-ping dnsutils -y
base_url=hc-jx5tis6bc.us-central1.gcp.f2e0a955bb84.cloud


while true;do
    resolved=$(dig +short cluster-mz-7.$1.$base_url)
    if [[ -n $resolved ]];then
        date
        echo "host name was resolved"
        echo -n "##\n##\n##\n"
        dig cluster-mz-7.$1.$base_url
        echo "trying command with external DNS"
        echo -n "##\n##\n##\n"
        redis-cli -h cluster-mz-7.$1.$base_url -p 6379 --user falkordb -a falkordb -c cluster nodes
        echo "Trying command with internal DNS"
        echo -n "##\n##\n##\n"
        redis-cli -h cluster-mz-7.$1 -p 6379 --user falkordb -a falkordb -c cluster nodes
        echo "Trying with ip"
        echo -n "##\n##\n##\n"
        redis-cli -h $resolved -p 6379 --user falkordb -a falkordb -c cluster nodes
        break
    else
        echo "host name not resolved yet"
    fi
done


while true;do
    result=$(ping cluster-mz-0)
    sleep 5
    if [[ $? -ne 0 ]];then
        exit 0
    fi
done