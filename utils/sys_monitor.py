import time
import csv
import logging
import sys
import subprocess
import json

log = logging.getLogger(__name__)


def get_docker_stats():
    """
    Query Docker for per-container CPU and memory usage.
    Returns (total_cpu_percent, total_mem_mb, container_count) for LNTest containers only.
    """
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             '{"name":"{{.Name}}","cpu":"{{.CPUPerc}}","mem":"{{.MemUsage}}"}'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return 0.0, 0.0, 0

        total_cpu = 0.0
        total_mem_mb = 0.0
        count = 0

        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            try:
                entry = json.loads(line)
                name = entry['name']

                # Only count LNTest containers (CC*, BM, InnocentNode)
                if not (name.startswith('CC') or name == 'BM' or name == 'InnocentNode'):
                    continue

                # Parse CPU: "12.34%" -> 12.34
                cpu_str = entry['cpu'].replace('%', '')
                total_cpu += float(cpu_str)

                # Parse memory: "123.4MiB / 64GiB" -> 123.4
                mem_str = entry['mem'].split('/')[0].strip()
                if 'GiB' in mem_str:
                    total_mem_mb += float(mem_str.replace('GiB', '')) * 1024
                elif 'MiB' in mem_str:
                    total_mem_mb += float(mem_str.replace('MiB', ''))
                elif 'KiB' in mem_str:
                    total_mem_mb += float(mem_str.replace('KiB', '')) / 1024

                count += 1
            except (json.JSONDecodeError, ValueError, KeyError):
                continue

        return total_cpu, total_mem_mb, count

    except (subprocess.TimeoutExpired, Exception):
        return 0.0, 0.0, 0


def monitor_loop(filename="data/system_metrics.csv", interval=1):

    print(f"Starting Docker container monitor. Logging to {filename} ...")

    with open(filename, mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "cpu_percent", "ram_used_mb", "ram_used_gb", "container_count"])

        try:
            while True:
                total_cpu, total_mem_mb, count = get_docker_stats()

                writer.writerow([
                    time.time(),
                    round(total_cpu, 2),
                    round(total_mem_mb, 2),
                    round(total_mem_mb / 1024, 4),
                    count
                ])

                f.flush()
                time.sleep(interval)

        except KeyboardInterrupt:
            print("\nMonitoring stopped.")


class HardwareMonitor:
    def __init__(self, output_file="system_metrics.csv"):
        self.output_file = output_file
        self._process = None

    def start(self):
        """Spawns this script as a separate background process."""
        if self._process is not None:
            return # Already running

        self._process = subprocess.Popen(
            [sys.executable, __file__, "--run-worker", self.output_file]
        )
        log.info(f"[Monitor] Started background process (PID: {self._process.pid})")

    def stop(self):
        """Kills the background process."""
        if self._process:
            log.info(f"[Monitor] [PID: {self._process.pid}] Stopping...")
            self._process.terminate()
            self._process.wait()
            self._process = None

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--run-worker":
        filename = sys.argv[2] if len(sys.argv) > 2 else "data/system_metrics.csv"
        monitor_loop(filename)
    else:
        monitor_loop()
