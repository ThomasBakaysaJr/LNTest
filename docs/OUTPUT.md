# Generated Data & Output

All experimental data is saved to the `data/` directory. The testbed generates distinct files for each test configuration based on parameter values, so different configurations never overwrite each other. However, re-running the exact same configuration will overwrite previously generated data for that configuration.

## Data Structure

Filenames follow the convention: `data/<variable>_<value>_<unique_id>_<type>`.

For takedown tests, filenames include a strategy marker: `T` for random takedown, `Ttargeted` for targeted takedown. Mode suffixes indicate the topology mode: `D` for dlnbot, `F` for dlnbot-formation, `X` for custom (e.g., `TD` for random takedown on dlnbot topology).

* **Propagation Data** (`*_time_data.json`)
  * `test_data`: one record per message, each carrying its own parameters (C&C node count, active nodes, injection count, the swept value) plus the propagation time, coverage percentage, nodes received, surviving total, and whether the network partitioned.
  * `total_times`: total setup time and total message-sending time.

* **Topology Snapshots** (`*_topology_data.json`)
  * Captures the surviving Lightning Network at the end of each test iteration: every node, its channels, channel capacity, and connection status.
  * For takedown tests this is the topology *after* removals; the removed nodes are listed in `orchestrator.log`.

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
