import psutil
import time
import csv
import sys
import subprocess

def monitor_loop(filename="data/system_metrics.csv", interval=1):
    
    print(f"Starting generic system monitor. Logging to {filename} ...")
    
    with open(filename, mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "cpu_percent", "ram_percent", "ram_used_gb"])
        
        try:
            while True:
                # 1. Get Metrics
                # interval=None means 'since last call' (non-blocking)
                cpu = psutil.cpu_percent(interval=None) 
                mem = psutil.virtual_memory()
                
                # 2. Log Data
                writer.writerow([
                    time.time(),
                    cpu,
                    mem.percent,
                    mem.used / (1024**3) # Convert to GB
                ])
                
                # 3. Flush to disk immediately (so you don't lose data if it crashes)
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

        # We call THIS file as a script again using sys.executable
        # This bypasses the GIL by creating a whole new Python instance
        self._process = subprocess.Popen(
            [sys.executable, __file__, "--run-worker", self.output_file]
        )
        print(f" [Monitor] Started background process (PID: {self._process.pid})")

    def stop(self):
        """Kills the background process."""
        if self._process:
            print(f" [Monitor] [PID: {self._process.pid}] Stopping...")
            self._process.terminate()
            self._process.wait()
            self._process = None

if __name__ == "__main__":
    # Check if we are being called as the worker
    if len(sys.argv) > 1 and sys.argv[1] == "--run-worker":
        # We are the child process! Run the loop.
        filename = sys.argv[2] if len(sys.argv) > 2 else "data/system_metrics.csv"
        monitor_loop(filename)
    else:
        # We are being run manually by the user
        monitor_loop()
