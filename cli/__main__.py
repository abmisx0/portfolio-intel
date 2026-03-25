import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cli.main import cli
if __name__ == "__main__":
    cli()
