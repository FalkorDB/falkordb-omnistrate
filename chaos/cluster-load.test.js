import redis from 'k6/experimental/redis';
import { check } from 'k6';
import exec from 'k6/execution';


const client = new redis.Client({
  socket: {
    dialTimeout: 5000,
    readTimeout: 5000,
    writeTimeout: 5000,
    poolTimeout: 5000,
  },
  cluster: {
    // Cluster options
    maxRedirects: 3,
    readOnly: true,
    routeByLatency: true,
    routeRandomly: true,
    nodes: [
      // Nodes URLs
    ]
  },
});

export const options = {
  stages: [
    { duration: '120s', target: 100 },
  ]
};

async function callServer() {
  const vuId = exec.vu.idInTest;
  const ok = await client.set(`a-${vuId}`, `${vuId}`).then(() => true).catch(() => false);
  check(ok, { 'set command succeeded': (ok) => ok });
}

export default function () {
  callServer();
}