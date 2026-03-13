# Troubleshooting

### Bitcoin error

If you encounter Bitcoin errors when a test starts (typically related to wallet loading), this usually means Bitcoin Core was already running and the script could not shut it down properly, or the machine was shut down while Bitcoin Core was still running.

Kill the process (may require `sudo`):

```bash
pkill -9 bitcoind
```

### Bitcoin lock error

If the tester reports it cannot acquire a lock on the regtest folder, Bitcoin Core was not shut down by the previous run. Press Ctrl+C to exit the tester and try again — it usually resolves immediately.

### Shared memory errors

If you see "Shared memory block not found" errors during propagation monitoring, this is typically caused by Python's resource tracker prematurely unlinking shared memory blocks. The current codebase includes fixes for this (unregistering SHM blocks from the resource tracker in both host and container processes). If it still occurs, clean up manually:

```bash
sudo rm -rf /dev/shm/CC*
```

### Channel creation timeout

If the test reports "Channels were not ready in time" and retries, this is usually a transient issue with container scheduling or gossip propagation timing. The test will automatically retry up to 5 times per iteration. If it consistently fails, try reducing the number of nodes or increasing the `NM_MAX_WAIT` environment variable.
