import json
import os
import time
import subprocess

from utils.config import cfg


def build_chain_edges(n, m):
    '''
    Generate the D-LNBot chain edge set.
    CC_i connects to CC_{max(1, i-m)} through CC_{i-1}.
    Returns a set of (from, to) tuples.
    '''
    edges = set()
    for i in range(2, n + 1):
        for j in range(max(1, i - m), i):
            edges.add((i, j))
    return edges


def load_and_validate_topology(file_path, n):
    '''
    Load a custom topology from a JSON file and validate it.

    Expected format:
    {
      "nodes": 50,
      "edges": [[2, 1], [3, 1], [3, 2], ...]
    }

    Each [from, to] means CC{from} opens a channel to CC{to}.
    Returns a set of (from, to) tuples.
    '''
    if not os.path.exists(file_path):
        print(f'  ERROR: Topology file not found: {file_path}', flush=True)
        return None

    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f'  ERROR: Could not parse topology file: {e}', flush=True)
        return None

    # Validate structure
    if 'edges' not in data:
        print(f'  ERROR: Topology file missing "edges" field.', flush=True)
        return None

    file_nodes = data.get('nodes', n)
    if file_nodes != n:
        print(f'  WARNING: Topology file specifies {file_nodes} nodes but LNTest is running {n} nodes. Using {n}.', flush=True)

    raw_edges = data['edges']
    edges = set()
    skipped_self = 0
    skipped_dup = 0
    skipped_range = 0

    for edge in raw_edges:
        if len(edge) != 2:
            print(f'  WARNING: Skipping malformed edge: {edge}', flush=True)
            continue
        src, dst = int(edge[0]), int(edge[1])

        # Self-loop check
        if src == dst:
            skipped_self += 1
            continue

        # Range check
        if src < 1 or src > n or dst < 1 or dst > n:
            skipped_range += 1
            continue

        # Duplicate check
        if (src, dst) in edges:
            skipped_dup += 1
            continue

        edges.add((src, dst))

    # Print warnings
    if skipped_self > 0:
        print(f'  WARNING: Filtered {skipped_self} self-loop(s).', flush=True)
    if skipped_dup > 0:
        print(f'  WARNING: Filtered {skipped_dup} duplicate edge(s).', flush=True)
    if skipped_range > 0:
        print(f'  WARNING: Filtered {skipped_range} out-of-range edge(s) (valid range: 1-{n}).', flush=True)

    if len(edges) == 0:
        print(f'  ERROR: No valid edges in topology file.', flush=True)
        return None

    # Connectivity check (BFS from node 1)
    from collections import deque
    adj = {i: set() for i in range(1, n + 1)}
    for src, dst in edges:
        adj[src].add(dst)
        adj[dst].add(src)

    visited = set()
    queue = deque([1])
    visited.add(1)
    while queue:
        node = queue.popleft()
        for neighbor in adj[node]:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)

    isolated = [i for i in range(1, n + 1) if i not in visited]
    if isolated:
        print(f'  WARNING: Graph is disconnected. {len(isolated)} node(s) unreachable from CC1: '
              f'{isolated[:10]}{"..." if len(isolated) > 10 else ""}', flush=True)
        print(f'  These nodes will not receive commands. Proceeding anyway.', flush=True)

    # Summary
    max_degree = max(len(adj[i]) for i in range(1, n + 1))
    min_degree = min(len(adj[i]) for i in range(1, n + 1))
    avg_degree = sum(len(adj[i]) for i in range(1, n + 1)) / n
    print(f'  Loaded {len(edges)} edges for {n} nodes '
          f'(avg_degree={avg_degree:.1f}, min={min_degree}, max={max_degree}).', flush=True)

    return edges


def build_topology(edges, cc_nodes):
    '''
    Build an arbitrary topology on a clean network from a set of edges.
    Each edge is a (from, to) tuple where from and to are CC node numbers.

    Uses multifundchannel to open all of a node's channels in a single
    on-chain transaction, avoiding UTXO exhaustion issues.

    Prerequisites: SKIP_CC_MANAGER=1 was set during setup_test() so
    no autonomous channels exist. This method only opens channels.
    '''
    if not cc_nodes:
        print('build_topology: No CC nodes found.', flush=True)
        return False

    def cc_num(container):
        return int(container.name.replace('CC', ''))
    cc_nodes_sorted = sorted(cc_nodes, key=cc_num)
    n = len(cc_nodes_sorted)

    def get_cli_error(output):
        if output[0]:
            try:
                err_data = json.loads(output[0].decode('utf-8'))
                if 'message' in err_data:
                    return err_data['message']
            except Exception:
                return output[0].decode('utf-8').strip()[:120]
        if output[1]:
            return output[1].decode('utf-8').strip()[:120]
        return 'unknown'

    def mine_blocks(num_blocks=6):
        bitcoin_cli = cfg.BITCOIN_CLI
        bitcoin_dir = cfg.BITCOIN_DIR
        if bitcoin_cli and bitcoin_dir:
            try:
                result = subprocess.run(
                    [bitcoin_cli, f'-datadir={bitcoin_dir}', '-regtest', 'getnewaddress'],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    addr = result.stdout.strip()
                    subprocess.run(
                        [bitcoin_cli, f'-datadir={bitcoin_dir}', '-regtest',
                         'generatetoaddress', str(num_blocks), addr],
                        capture_output=True
                    )
            except Exception as e:
                print(f'    Warning: could not mine blocks: {e}', flush=True)

    # ── Phase 1: Gather node info ──
    print(f'  Phase 1: Gathering node info for {n} nodes...', flush=True)
    node_info = {}

    for container in cc_nodes_sorted:
        num = cc_num(container)
        try:
            exit_code, output = container.exec_run(
                'lightning-cli --regtest getinfo', demux=True
            )
            if exit_code == 0 and output[0]:
                info = json.loads(output[0].decode('utf-8'))
                binding = info.get('binding', [{}])
                addr = binding[0].get('address', '127.0.0.1') if binding else '127.0.0.1'
                port = binding[0].get('port', 19849 + num) if binding else 19849 + num
                node_info[num] = {
                    'pubkey': info['id'],
                    'address': f"{info['id']}@{addr}:{port}",
                    'container': container,
                    'name': container.name
                }
            else:
                print(f'  ERROR: Could not get info for CC{num}', flush=True)
                return False
        except Exception as e:
            print(f'  ERROR: Exception getting info for CC{num}: {e}', flush=True)
            return False

    print(f'  Target: {len(edges)} edges for {n} nodes', flush=True)

    # ── Phase 2: Open channels using multifundchannel ──
    # Group edges by source node so each node opens all its outbound channels in one tx
    from collections import defaultdict
    outbound = defaultdict(list)
    for src, dst in edges:
        outbound[src].append(dst)

    print(f'  Phase 2: Opening channels via multifundchannel...', flush=True)
    total_opened = 0
    total_failed = 0

    for src_num in sorted(outbound.keys()):
        targets = outbound[src_num]
        from_info = node_info[src_num]
        container = from_info['container']

        # Connect to all targets first
        for dst_num in targets:
            to_info = node_info[dst_num]
            container.exec_run(
                f'lightning-cli --regtest connect {to_info["address"]}', demux=True
            )

        # Build multifundchannel destinations
        destinations = []
        for dst_num in targets:
            to_info = node_info[dst_num]
            destinations.append({
                "id": to_info['pubkey'],
                "amount": "50000",
                "push_msat": 25000000
            })

        dest_json = json.dumps(destinations)
        cmd = f"lightning-cli --regtest multifundchannel '{dest_json}'"

        exit_code, output = container.exec_run(cmd, demux=True)

        target_names = ','.join([f'CC{j}' for j in targets])
        if exit_code == 0:
            total_opened += len(targets)
            print(f'    CC{src_num} -> [{target_names}]: {len(targets)} channels opened', flush=True)
        else:
            err = get_cli_error(output)
            print(f'    CC{src_num} -> [{target_names}]: FAILED - {err}', flush=True)
            total_failed += len(targets)

        # Mine after each source node to confirm the funding tx
        mine_blocks(6)
        time.sleep(1)

    print(f'  Opened {total_opened} channels ({total_failed} failed).', flush=True)

    # Final mining to ensure all channels reach CHANNELD_NORMAL
    print(f'    Mining 20 blocks to finalize...', flush=True)
    mine_blocks(20)
    wait_time = max(15, n // 3)
    print(f'    Waiting {wait_time}s for channels to activate...', flush=True)
    time.sleep(wait_time)

    # ── Phase 3: Verify final topology ──
    print(f'  Phase 3: Verifying topology...', flush=True)
    degree_counts = {}
    for num, info in node_info.items():
        try:
            exit_code, output = info['container'].exec_run(
                'lightning-cli --regtest listpeerchannels', demux=True
            )
            if exit_code == 0 and output[0]:
                result = json.loads(output[0].decode('utf-8'))
                normal = [c for c in result.get('channels', []) if c.get('state') == 'CHANNELD_NORMAL']
                degree_counts[num] = len(normal)
        except Exception:
            degree_counts[num] = 0

    if degree_counts:
        avg = sum(degree_counts.values()) / len(degree_counts)
        # Compute expected degrees from the edge set
        expected_degrees = {i: 0 for i in range(1, n + 1)}
        for src, dst in edges:
            expected_degrees[src] += 1
            expected_degrees[dst] += 1
        expected_avg = sum(expected_degrees.values()) / n

        print(f'  Final topology: avg_degree={avg:.1f} (expected={expected_avg:.1f}), '
              f'min={min(degree_counts.values())}, max={max(degree_counts.values())}', flush=True)

        mismatches = 0
        for i in sorted(degree_counts.keys()):
            expected = expected_degrees[i]
            actual = degree_counts[i]
            if actual != expected:
                mismatches += 1
                print(f'    CC{i}: {actual} channels MISMATCH (expected {expected})', flush=True)

        # Show edge nodes for reference
        edge_nodes = [i for i in sorted(degree_counts.keys())
                     if degree_counts[i] != max(degree_counts.values())]
        if edge_nodes and mismatches == 0:
            # Show first/last few nodes
            show = edge_nodes[:3] + edge_nodes[-3:] if len(edge_nodes) > 6 else edge_nodes
            for i in show:
                print(f'    CC{i}: {degree_counts[i]} channels OK', flush=True)

        if mismatches == 0:
            print(f'  All nodes match expected topology!', flush=True)
        else:
            print(f'  {mismatches} nodes have mismatched channel counts.', flush=True)

    print(f'  Topology build complete.', flush=True)
    return True
