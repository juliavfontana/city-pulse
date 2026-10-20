"""Paris Cultural Pulse — entry point. Calls collect.py with city=paris."""
import sys
from collect import main

sys.argv = [sys.argv[0], "--city", "paris"] + sys.argv[1:]
main()
