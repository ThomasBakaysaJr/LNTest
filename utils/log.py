'''
Centralized logging configuration for LNTest host-side code.

Console output matches current print() behavior (message only).
File handler adds timestamps, levels, and module names.

Usage:
    from utils.log import setup_logging, add_file_handler
    setup_logging()                    # call once at startup
    add_file_handler('data/run.log')   # optional, per test run
'''
import logging
import os
import sys

_file_handler = None


def setup_logging(level=logging.INFO):
    '''Configure root logger with console handler (stdout).'''
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter('%(message)s'))
    root.addHandler(console)


def add_file_handler(filepath):
    '''Add/replace a file handler for test-run logging.'''
    global _file_handler
    root = logging.getLogger()
    if _file_handler:
        root.removeHandler(_file_handler)
    os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)
    _file_handler = logging.FileHandler(filepath)
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)-7s] %(name)s: %(message)s'
    ))
    root.addHandler(_file_handler)
