import glob
import time

LOG_PREFIX = 'NodeManagerComms/logs/cc_log*'
NOISE_PREFIX = 'NodeManagerComms/logs/noise_log*'
BM_PREFIX = 'BotMasterComms/bm_log.log'
UPDATE_INT = 0.2

LOG_FILE = 'master_log.log'
PREFIXES = [LOG_PREFIX, NOISE_PREFIX, BM_PREFIX]
ALL_FILES = []
OPEN_FILES = []

def open_logs():
    '''
    Open all the log files
    '''
    global OPEN_FILES
    files = []
    files_to_open = []

    for prefix in PREFIXES:
        files += (glob.glob(prefix))

    # we are going to read the last line for each file and display those
    for file in files:
        if file in OPEN_FILES:
            continue
        f = open(file, 'r')
        ALL_FILES.append(f)
        files_to_open.append(f)
        OPEN_FILES.append(file)

    # read all the lines that exists so the write_master is only trying to read new lines
    for file in files_to_open:
        record(f"Starting recording for {file}")
        line = file.readline()
        while line:
            proc_line = line.strip()
            if 'warning' in proc_line.lower() or 'error' in proc_line.lower():
                record(proc_line)
            line = file.readline()

def write_master():
    ''''
    main loop, write all warnings going into loop
    '''
    while True:
        open_logs()
        for file in ALL_FILES:
            line = file.readline()
            if line:
                proc_line = line.strip()
                if 'warning' in proc_line.lower() or 'error' in proc_line.lower():
                    record(proc_line)
        time.sleep(UPDATE_INT)

def record(line):
    with open(LOG_FILE, 'a') as log_file:
        log_file.write(f'{line}\n')

def main():
    # clear out the master log file
    global LOG_FILE
    with open(LOG_FILE, 'w') as f:
        pass

    # Main loop, update the master log file
    open_logs()
    write_master()

            
    
if __name__ == "__main__":
    main()
