# Generated Data & Output

All experimental data is saved to the `data/` directory. The testbed generates distinct files for each test configuration based on parameter values, so different configurations never overwrite each other. However, re-running the exact same configuration will overwrite previously generated data for that configuration.

## Data Structure

Filenames follow the convention: `data/<variable>_<value>_<unique_id>_<type>`.

For takedown tests, filenames include a strategy marker: `T` for random takedown, `Ttargeted` for targeted takedown. Mode suffixes indicate the topology mode: `D` for dlnbot, `F` for dlnbot-formation, `X` for custom (e.g., `TD` for random takedown on dlnbot topology).

* **Propagation Data** (`*_time_data.json`)
  * Contains the time it took for each message to propagate through the network.
  * Includes metadata about the test configuration (number of C&C nodes, active nodes, etc.).
  * Records total setup time and total message sending time.
  * For takedown tests, also includes coverage data: coverage percentage, number of nodes that received the message, total surviving nodes, and whether the network partitioned.

* **Topology Snapshots** (`*_topology_data.json`)
  * Captures the state of the Lightning Network at the end of each test iteration.
  * Lists every surviving node, its channels, channel capacity, and connection status.
  * For takedown tests, the metadata includes which nodes were removed and their channel details at the time of removal.

* **System Metrics** (`*_system_metrics.csv`)
  * Logs CPU and RAM usage of the host machine throughout the test.
  * Useful for analyzing the hardware overhead of running many Docker containers.

* **Execution Logs** (`*_total_times_log.json`)
  * A running log of how long each test run took to execute.
  * Timestamped by date (e.g., `YYYY-MM-DD_total_times_log.json`).

* **Orchestrator Log** (`orchestrator.log`)
  * Detailed log of orchestrator decisions, warnings, and errors with timestamps and log levels.

## Node Logs

Detailed logs for debugging specific node behaviors are stored in:
* `cc_node/logs/` — individual logs for every C&C node.
* `botmaster/logs/` — logs for the Botmaster node.
