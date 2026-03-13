# Troubleshooting

### Bitcoin Core errors

If you encounter Bitcoin-related errors when a test starts (wallet loading, lock file, RPC failures), this usually means bitcoind was not shut down cleanly by a previous run. Kill it and try again:

```bash
sudo pkill -9 bitcoind
```

### Stale containers from a crashed run

If a previous run was interrupted (Ctrl+C, crash, reboot), leftover Docker containers can cause port conflicts or name collisions on the next run. Clean up everything before retrying:

```bash
sudo ./scripts/cleanup.sh all
```

### Too many open files

Large tests (100+ nodes) require many file descriptors. LNTest raises the soft limit to 65536 at startup, but this fails silently if the OS hard limit is lower. If you see "Too many open files" errors, increase the hard limit:

```bash
# Check current limits
ulimit -n -H

# Raise permanently in /etc/security/limits.conf:
# * hard nofile 65536
```

### Docker permission denied

LNTest requires `sudo` because it manages Docker containers and shared memory. If you see permission errors, make sure you are running with `sudo`:

```bash
sudo venv/bin/python3 lntest.py run cc_count --num-msg 3
```

Also verify the Docker daemon is running:

```bash
sudo systemctl status docker
```

### Coverage stalled warnings

During takedown tests, you may see warnings like `"Coverage stalled at 30/45 nodes for 60s. Network likely partitioned."` This is expected behavior, not an error — it means the takedown successfully partitioned the network. The test records the partial coverage as a valid data point and advances to the next iteration.

### Large tests appear stuck

Tests with many nodes are slow by design. A 100-node test can take 30+ minutes per iteration due to channel funding, gossip propagation, and block confirmations. Tests using `--dlnbot-formation` are even slower because containers are launched with staggered delays (10–90 seconds each) to simulate realistic deployment timing.
