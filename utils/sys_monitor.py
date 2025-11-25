import psutil
import time
import csv
import os
from datetime import datetime

def monitor_system(output_suffix="system_metrics.csv", interval=1):
    filename = datetime.now().strftime(f"data/%Y-%m-%d-%H-%M_{output_suffix}")
    # Create file and write headers if it doesn't exist
    file_exists = os.path.isfile(filename)
    
    print(f"Starting generic system monitor. Logging to {filename}...")
    
    with open(filename, mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
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

if __name__ == "__main__":
    # Run psutil once to initialize the CPU counter (it needs a 'previous' state)
    psutil.cpu_percent(interval=None) 
    monitor_system()