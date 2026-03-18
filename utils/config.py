'''
Centralized configuration for LNTest host-side Python code.

Loads config.env once via dotenv and provides typed, validated access.
All host-side Python files should import from here instead of calling
os.getenv() directly.

Container scripts (botmaster.py, cc_manager.py, message_relay.py, ln_checker.py)
are NOT affected — they receive env vars from Docker.
'''
import logging
import os
import sys
from dotenv import load_dotenv

log = logging.getLogger(__name__)


class _Config:
    '''Singleton config loaded from config.env.'''

    def __init__(self):
        self._loaded = False

    def load(self, env_path='config.env'):
        '''Load config.env and populate typed attributes.'''
        if self._loaded:
            return
        load_dotenv(env_path)
        self._loaded = True

        # --- Bitcoin ---
        self.BITCOIN_CLI = os.getenv('BITCOIN_CLI')
        self.BITCOIN_DIR = os.getenv('BITCOIN_DIR')
        self.RPC_USER = os.getenv('RPC_USER')
        self.RPC_PASSWORD = os.getenv('RPC_PASSWORD')

        # --- Lightning / Docker ---
        self.LNTEST_VERSION = os.getenv('LNTEST_VERSION')

        # --- Script paths ---
        self.MINER_SCRIPT = os.getenv('MINER_SCRIPT')
        self.KILL_NODES_BASH = os.getenv('KILL_NODES_BASH')
        self.INIT_BOTNET_BASH = os.getenv('INIT_BOTNET_BASH')
        self.CREATE_CC_SERVER_BASH = os.getenv('CREATE_CC_SERVER_BASH')
        self.RESTART_BITCOIND_BASH = os.getenv('RESTART_BITCOIND_BASH')
        self.FUND_WALLETS_BASH = os.getenv('FUND_WALLETS_BASH')

        # --- Data directories ---
        self.TEST_STATE_DIR = os.getenv('TEST_STATE_DIR')
        self.TEST_DATA_DIR = os.getenv('TEST_DATA_DIR')

        # --- Node names ---
        self.BOTMASTER_NODE = os.getenv('BOTMASTER_NODE', 'BM')
        self.BOTMASTER_SCRIPT = os.getenv('BOTMASTER_SCRIPT')
        self.INNOCENT_NODE = os.getenv('INNOCENT_NODE', 'InnocentNode')

        # --- Container paths ---
        self.BOT_MASTER_CONTAINER_DIR = os.getenv('BOT_MASTER_CONTAINER_DIR')

        # --- Address list files ---
        self.NODE_MANAGER_ADDRESS_LIST = os.getenv('NODE_MANAGER_ADDRESS_LIST')
        self.BOT_MASTER_ADDRESS_LIST = os.getenv('BOT_MASTER_ADDRESS_LIST')

        # --- Wait times (canonical defaults match config.env.template) ---
        self.NM_SLEEP = int(os.getenv('NM_SLEEP', 1))
        self.NM_MAX_WAIT = int(os.getenv('NM_MAX_WAIT', 450))
        self.NM_MAX_WAIT_MULT = int(os.getenv('NM_MAX_WAIT_MULT', 2))

        # --- Mining ---
        self.INITIAL_MINING_BLOCKS = int(os.getenv('INITIAL_MINING_BLOCKS', 101))
        self.REGULAR_MINING_BLOCKS = int(os.getenv('REGULAR_MINING_BLOCKS', 1))

        # --- Channel / node behavior (passed to containers via node_config.json) ---
        self.DISCOVERY_RULE = int(os.getenv('DISCOVERY_RULE', 19))
        self.BOTMASTER_RULE = int(os.getenv('BOTMASTER_RULE', 123123))
        self.NODE_CHANNEL_SLEEP = int(os.getenv('NODE_CHANNEL_SLEEP', 10))
        self.NODE_UPDATE_INTERVAL = float(os.getenv('NODE_UPDATE_INTERVAL', 1.5))
        self.NODE_BALANCE_COUNTER = int(os.getenv('NODE_BALANCE_COUNTER', 3))
        self.MIN_CHANNEL_CAPACITY = int(os.getenv('MIN_CHANNEL_CAPACITY', 50000))
        self.MAX_CHANNEL_CAPACITY = int(os.getenv('MAX_CHANNEL_CAPACITY', 150000))
        self.NODE_SLEEP_INTERVAL = int(os.getenv('NODE_SLEEP_INTERVAL', 3))
        self.NODE_RETRY_INTERVAL = int(os.getenv('NODE_RETRY_INTERVAL', 5))

        # --- Shared memory ---
        self.SHM_OVERHEAD = int(os.getenv('SHM_OVERHEAD', 512))
        self.SHM_PER_PEER = int(os.getenv('SHM_PER_PEER', 256))
        self.SHM_BUFFER = float(os.getenv('SHM_BUFFER', 1.2))

    def validate(self):
        '''Check that critical config values are set. Call after load().'''
        required = [
            'BITCOIN_CLI', 'BITCOIN_DIR', 'LNTEST_VERSION',
            'TEST_STATE_DIR', 'TEST_DATA_DIR',
            'INIT_BOTNET_BASH', 'CREATE_CC_SERVER_BASH',
            'KILL_NODES_BASH', 'RESTART_BITCOIND_BASH',
            'FUND_WALLETS_BASH', 'MINER_SCRIPT',
            'NODE_MANAGER_ADDRESS_LIST', 'BOT_MASTER_ADDRESS_LIST',
        ]
        missing = [name for name in required if getattr(self, name, None) is None]
        if missing:
            log.error(f'Missing required config values: {", ".join(missing)}')
            log.error('Run setup.sh or check config.env.')
            sys.exit(1)


# Module-level singleton — import as: from utils.config import cfg
cfg = _Config()
