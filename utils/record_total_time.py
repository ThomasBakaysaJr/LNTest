import time
import json

def record_total_time(total_time, config, output_file="total_time_log.json"):
    log_entry = {
        "timestamp": time.time(),
        "total_time": total_time,
        "config": config
    }
    
    # Append the log entry to the JSON file
    try:
        with open(output_file, 'r') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = []
    
    data.append(log_entry)
    
    with open(output_file, 'w') as f:
        json.dump(data, f, indent=4)