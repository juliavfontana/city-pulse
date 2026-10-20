"""Berlin Cultural Pulse — entry point. Calls collect.py with city=berlin."""
import sys
from collect import main

sys.argv = [sys.argv[0], "--city", "berlin"] + sys.argv[1:]
main()
