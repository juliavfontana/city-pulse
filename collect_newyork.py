"""New York Cultural Pulse — entry point. Calls collect.py with city=new_york."""
import sys
from collect import main

sys.argv = [sys.argv[0], "--city", "new_york"] + sys.argv[1:]
main()
