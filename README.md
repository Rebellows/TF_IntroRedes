# Token Ring — Local Testing with Docker

## Requirements

- Docker Desktop (Mac/Windows) or Docker Engine + Compose (Linux)
- No other changes to the source code needed

---

## Quick start

### 1. Build and start all 3 nodes

```bash
docker compose up --build
```

This brings up containers `ring-A`, `ring-B`, `ring-C` on the same virtual
subnet (172.20.0.0/24). Broadcast reaches all three.

---

### 2. Open an interactive terminal for each node

In **three separate terminal windows**, attach to each container:

```bash
# Terminal 1
docker attach ring-A

# Terminal 2
docker attach ring-B

# Terminal 3
docker attach ring-C
```

To detach without stopping a container use **Ctrl-P, Ctrl-Q**.

---

### 3. Send messages

Once attached, type:

```
> B Hello from A!
> C Testing the ring
```

Format: `<destination nickname> <message>`

---

## Testing specific scenarios

### Test fault injection (node B has 10% error probability by default)

Send several messages through B (A → C or C → A) and watch for NAK + retransmit logs.

### Test token loss

Stop one container while it holds the token:

```bash
docker stop ring-B
```

Node A (the controller) should detect the timeout and generate a new token.
Node B is removed from the ring after 30 seconds of missing heartbeats.

### Test a new node joining

```bash
docker compose up --build node-c   # if C was stopped
```

Or start a fourth node:

```bash
docker run --rm -it \
  --network ring_network_ring \
  --env NICKNAME=D \
  --env TOKEN_TIMEOUT=5.0 \
  --env MIN_TOKEN_INTERVAL=0.5 \
  --env ERROR_PROBABILITY=0.0 \
  ring_network-node-a   # reuse the same image
```

---

## Useful commands

```bash
# View logs without attaching
docker logs -f ring-A

# Run with DEBUG logging
docker compose run --rm -e NICKNAME=X node-a python3 main.py --log DEBUG

# Tear everything down
docker compose down
```

---

## How broadcast works in Docker

Docker bridge networks support directed broadcast to the subnet address
(172.20.0.255 for a /24). The code uses `255.255.255.255` (limited broadcast),
which Docker also forwards within the bridge network — so no code changes are
needed.

---

## Common issues

| Problem | Fix |
|---|---|
| `Address already in use` on port 6000 | Another process is using port 6000. `lsof -i :6000` to find it. |
| Containers can't reach each other | Make sure they are on the `ring` network: `docker network inspect ring_network_ring` |
| `docker attach` shows nothing | Press Enter once — the input loop is waiting for you. |
