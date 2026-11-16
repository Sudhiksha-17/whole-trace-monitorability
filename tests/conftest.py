import os
import sys

# make `from src import ...` work, the same way the pipeline scripts do
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
