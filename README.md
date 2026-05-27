# LNTest

LNTest is a reproducible testbed for deploying and evaluating Lightning Network (LN)-based botnets on Bitcoin `regtest`. It implements the command-and-control (C&C) overlay and autonomous formation protocol of [D-LNBot](https://ieeexplore.ieee.org/document/10198749/), and also runs arbitrary user-defined overlays, so researchers can measure how topology shapes command propagation and resilience to takedowns. Every node is a real Core Lightning (CLN) instance in its own Docker container, all backed by a single Bitcoin Core node on the host. After setup, runs are fully offline.

This work was **accepted to [ARES 2026](https://www.ares-conference.eu/)** (International Conference on Availability, Reliability and Security).

## Citation

If you use LNTest, please cite the ARES 2026 paper (to appear):

```bibtex
@inproceedings{lntest2026,
  author    = {Thomas Bakaysa and Abdul-Salem Byeibitkhan and Jesus Maria Romo Diaz de Leon and Tag Kalat and Joshua Kramer and Estela Rodriguez and Abraham Watkins and Abdullah Aydeger and Ahmet Kurt},
  title     = {{LNTest}: A Testbed for Evaluating Bitcoin Lightning Network-Based Botnets},
  booktitle = {Proceedings of the 21st International Conference on Availability, Reliability and Security (ARES)},
  publisher = {Springer},
  year      = {2026}
}
```

## Architecture

Five components:

1. **Bitcoin Core**: host-side `regtest` node; the shared on-chain layer.
2. **C&C servers**: CLN containers that form the overlay by opening channels to one another; commands spread by concurrent keysend flooding.
3. **Botmaster**: a CLN container that injects commands by opening a channel to one or more C&C servers and sending keysends.
4. **Innocent node**: a CLN container used as the rendezvous point for peer discovery during autonomous formation.
5. **Orchestrator (`lntest.py`)**: host-side Python that drives each experiment, monitors propagation via POSIX shared memory, and records results.

## Quick start

Install per [docs/SETUP.md](docs/SETUP.md), then run the sanity check (4 nodes, 1 message):

```bash
sudo venv/bin/python3 lntest.py small
```

Run an experiment:

```bash
sudo venv/bin/python3 lntest.py run <test> [options]
```

`<test>` is one of `cc_count`, `active_nodes`, `injection`, `takedown_random`, `takedown_targeted`, grouped as **scalability** (how propagation scales with botnet size and overlay width), **injection** (whether more botmaster entry points help), and **resilience** (how coverage degrades as nodes are removed). Sweeps, flags, and topology modes are documented in [docs/TESTS.md](docs/TESTS.md).

## Documentation

- [docs/SETUP.md](docs/SETUP.md): installation, configuration, and resetting the testbed
- [docs/TOPOLOGIES.md](docs/TOPOLOGIES.md): overlay modes (D-LNBot chain, autonomous formation, custom) and the topology-file format
- [docs/TESTS.md](docs/TESTS.md): tests, flags, sweep ranges, mode compatibility, coverage and partition detection
- [docs/OUTPUT.md](docs/OUTPUT.md): generated data files and naming

## Reproduce the results in the paper

`run_all_paper_tests.sh` in the repo root runs every experiment from Sections 4.1-4.5 back to back and unattended, so you can kick it off and come back later. Results land in `data/`, and each test's terminal output is saved under `data/terminal_logs/`.

The suite runs for hours, so launch it inside `tmux` and keep the machine from sleeping while it works:

```bash
tmux new -s lntest
sudo systemd-inhibit --what=sleep:idle:handle-lid-switch --why=LNTest ./run_all_paper_tests.sh
```

The full run took about 13 hours on our hardware: a Supermicro tower with a 16-core AMD Ryzen Threadripper PRO 3955WX CPU and 64 GB of DDR4 3200 MT/s RAM, running Ubuntu 26.04 on a 1 TB Toshiba KXG60ZNV1T02 NVMe SSD (PCIe Gen3 ×4). Expect longer on slower hardware.

To preview the plan without running anything, use `DRY_RUN=1 ./run_all_paper_tests.sh`. The output paths and the per-test timeout are configurable through environment variables, all documented at the top of the script.

## Issues

Spot a bug or have a question? Please [open an issue on GitHub](https://github.com/ThomasBakaysaJr/LNTest/issues).
